(function () {
    const wrapper = document.querySelector('[data-admin-chats-users]');
    const modal = document.getElementById('adminChatModal');

    if (!wrapper || !modal) {
        return;
    }

    const t = (key, fallback) => {
        if (typeof window.adminT === 'function') {
            return window.adminT(key, fallback);
        }
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const notify = (message) => {
        if (typeof window.notifyError === 'function') {
            window.notifyError(message);
        }
    };

    const dom = {
        modal,
        openButton: document.getElementById('openAdminChatsModalButton'),
        closeButton: document.getElementById('closeAdminChatsModalButton'),
        trigger: document.getElementById('userSelectTrigger'),
        dropdown: document.getElementById('userDropdown'),
        dropdownArrow: document.getElementById('dropdownArrow'),
        selectedName: document.getElementById('selectedName'),
        selectedEmail: document.getElementById('selectedEmail'),
        chatList: document.getElementById('chatList'),
        messagesArea: document.getElementById('messagesArea'),
        messagesHeader: document.getElementById('messagesHeader'),
        reasonOverlay: document.getElementById('viewReasonOverlay'),
        reasonForm: document.getElementById('viewReasonForm'),
        reasonInput: document.getElementById('viewReasonInput'),
        reasonCancelButton: document.getElementById('viewReasonCancelButton'),
        sidebarToggle: document.getElementById('adminChatSidebarToggle'),
        sidebarScrim: document.getElementById('adminChatSidebarScrim'),
    };

    const state = {
        dropdownOpen: false,
        users: [],
        usersLoaded: false,
        selectedUser: null,
        pendingUser: null,
        reason: '',
        chats: [],
        selectedChatId: null,
        modalOpen: false,
        reasonModalOpen: false,
        mobileSidebarOpen: false,
        modalLastFocusedElement: null,
        reasonLastFocusedElement: null,
    };

    const API = {
        users: '/api/v1/admin/users',
        chats: '/api/v1/admin/user/chats',
        chatMessages: '/api/v1/admin/user/chat/messages',
    };

    const userDropdownController = window.createDropdownController?.({
        id: 'admin-chats-user-dropdown',
        trigger: dom.trigger,
        dropdown: dom.dropdown,
        root: wrapper,
        dropdownOpenClass: 'show',
        triggerOpenClass: 'active',
        bindTrigger: false,
        openTargets: [
            { element: dom.dropdownArrow, className: 'open' },
        ],
        onToggle: ({ isOpen }) => {
            state.dropdownOpen = isOpen;
        },
    });

    const FALLBACKS = {
        selectUser: 'Select user',
        chooseFromList: 'Choose from list',
        selectConversation: 'Select a conversation',
        selectUserToBegin: 'Select a user to begin',
        selectConversationToView: 'Select a conversation to view messages',
        selectUserToView: 'Select a user to view their conversations',
        noConversations: 'No conversations found',
        noMessages: 'No messages in this conversation',
        loading: 'Loading…',
        failedToLoad: 'Failed to load',
        loadingUsers: 'Loading users…',
        noUsersAvailable: 'No users available',
        failedToLoadUsers: 'Failed to load users',
        failedToLoadMessages: 'Failed to load messages',
        untitled: 'Untitled',
        conversation: 'Conversation',
        assistant: 'Assistant',
        user: 'User',
        unknownUser: 'Unknown user',
        roleUnknown: 'Role unknown',
        unknownDate: 'Unknown',
        noContent: '[No content]',
        reasoning: 'Reasoning',
        toolCall: 'Tool call',
        toolCallPrefix: 'Tool call: {name}',
        argsPrefix: 'Args: {args}',
        widgetOutput: 'Widget output',
        fileAttachment: 'File attachment',
        fileAttachmentNamed: 'File attachment: {name}',
        attachmentImages: 'Images: {count}',
        attachmentVideos: 'Videos: {count}',
        attachmentAudio: 'Audio: {count}',
        attachmentDocuments: 'Documents: {count}',
        failedUsersFetch: 'Failed to load users.',
        failedChatsFetch: 'Failed to fetch chats.',
        failedMessagesFetch: 'Failed to fetch chat messages.',
    };

    let escapeRegistration = null;

    function text(key, fallbackKey) {
        return t(key, FALLBACKS[fallbackKey] || fallbackKey || key);
    }

    function registerEscapeHandler() {
        if (escapeRegistration || typeof window.registerEscapeHandler !== 'function') {
            return;
        }

        escapeRegistration = window.registerEscapeHandler({
            id: 'admin-chat-modal',
            priority: 60,
            isActive: () => state.modalOpen || state.reasonModalOpen,
            close: () => {
                if (state.reasonModalOpen) {
                    hideReasonModal();
                    return;
                }
                if (state.mobileSidebarOpen) {
                    closeMobileSidebar({ restoreFocus: true });
                    return;
                }
                hideModal();
            },
        });
    }

    function setBodyModalState() {
        document.body.classList.toggle('admin-chat-modal-open', state.modalOpen);
    }

    function setParentDialogSuppressed(suppressed) {
        const dialog = dom.modal.querySelector('[role="dialog"]');
        if (!dialog) {
            return;
        }
        dialog.toggleAttribute('inert', suppressed);
        if (suppressed) {
            dialog.setAttribute('aria-hidden', 'true');
        } else {
            dialog.removeAttribute('aria-hidden');
        }
    }

    function setMobileSidebarState(open, options = {}) {
        const shouldOpen = Boolean(open);
        state.mobileSidebarOpen = shouldOpen;
        dom.modal.classList.toggle('admin-chat-mobile-sidebar-open', shouldOpen);
        dom.sidebarToggle?.setAttribute('aria-expanded', String(shouldOpen));

        if (!shouldOpen) {
            closeDropdown();
        }

        if (options.restoreFocus && dom.sidebarToggle instanceof HTMLElement) {
            dom.sidebarToggle.focus();
        }
    }

    function openMobileSidebar() {
        setMobileSidebarState(true);
    }

    function closeMobileSidebar(options = {}) {
        setMobileSidebarState(false, options);
    }

    function toggleMobileSidebar() {
        setMobileSidebarState(!state.mobileSidebarOpen, { restoreFocus: state.mobileSidebarOpen });
    }

    function toggleDropdown(force) {
        if (!userDropdownController) {
            return;
        }
        const shouldOpen = typeof force === 'boolean' ? force : !userDropdownController.isOpen();
        userDropdownController[shouldOpen ? 'open' : 'close']({ reason: 'api' });
    }

    function closeDropdown() {
        if (!state.dropdownOpen) {
            return;
        }
        toggleDropdown(false);
    }

    function formatUserLabel(user) {
        const name = [user.first_name, user.last_name]
            .map((part) => (part || '').trim())
            .filter(Boolean)
            .join(' ');
        return name || user.email || text('unknown_user', 'unknownUser');
    }

    function renderUserSelection() {
        if (!state.selectedUser) {
            dom.selectedName.textContent = text('select_user', 'selectUser');
            dom.selectedEmail.textContent = text('choose_from_list', 'chooseFromList');
            return;
        }

        dom.selectedName.textContent = formatUserLabel(state.selectedUser);
        dom.selectedEmail.textContent = state.selectedUser.email || '';
    }

    function renderUserDropdown(options = {}) {
        const { loading = false, error = '' } = options;
        dom.dropdown.innerHTML = '';

        let content = null;
        if (loading) {
            content = document.createElement('div');
            content.className = 'user-option empty';
            content.textContent = text('loading_users', 'loadingUsers');
        } else if (error) {
            content = document.createElement('div');
            content.className = 'user-option empty';
            content.textContent = error;
        } else if (!state.users.length) {
            content = document.createElement('div');
            content.className = 'user-option empty';
            content.textContent = text('no_users_available', 'noUsersAvailable');
        }

        if (content) {
            dom.dropdown.appendChild(content);
            return;
        }

        const fragment = document.createDocumentFragment();
        state.users.forEach((user) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'user-option';
            button.dataset.userId = user.id;
            if (state.selectedUser?.id === user.id) {
                button.classList.add('selected');
            }

            const contentWrapper = document.createElement('div');
            contentWrapper.className = 'user-option-content';

            const nameEl = document.createElement('div');
            nameEl.className = 'user-option-name';
            nameEl.textContent = formatUserLabel(user);
            contentWrapper.appendChild(nameEl);

            if (user.email) {
                const emailEl = document.createElement('div');
                emailEl.className = 'user-option-email';
                emailEl.textContent = user.email;
                contentWrapper.appendChild(emailEl);
            }

            const metaEl = document.createElement('div');
            metaEl.className = 'user-option-meta';
            metaEl.textContent = `${user.role || text('role_unknown', 'roleUnknown')}${user.group_name ? ` · ${user.group_name}` : ''}`;
            contentWrapper.appendChild(metaEl);

            button.appendChild(contentWrapper);
            button.addEventListener('click', () => handleUserOptionClick(user));
            fragment.appendChild(button);
        });

        dom.dropdown.appendChild(fragment);
    }

    async function fetchUsers() {
        if (state.usersLoaded) {
            return;
        }

        renderUserDropdown({ loading: true });

        try {
            const response = await window.authedFetch(API.users, { method: 'GET' });
            if (!response.ok) {
                throw new Error(text('failed_to_load_users', 'failedToLoadUsers'));
            }

            const users = await response.json();
            state.users = Array.isArray(users) ? users : [];
            state.usersLoaded = true;
            renderUserDropdown();
        } catch (error) {
            console.error('Failed to fetch users', error);
            renderUserDropdown({ error: error?.message || text('failed_to_load_users', 'failedToLoadUsers') });
            notify(error?.message || text('failed_to_load_users', 'failedUsersFetch'));
        }
    }

    function showModal() {
        if (state.modalOpen) {
            return;
        }

        registerEscapeHandler();
        state.modalOpen = true;
        state.modalLastFocusedElement = document.activeElement;
        dom.modal.hidden = false;
        dom.modal.setAttribute('aria-hidden', 'false');
        setBodyModalState();
        closeDropdown();
        openMobileSidebar();
        fetchUsers();
        requestAnimationFrame(() => dom.closeButton?.focus());
    }

    function hideModal() {
        if (!state.modalOpen) {
            return;
        }

        state.modalOpen = false;
        closeDropdown();
        closeMobileSidebar();
        state.pendingUser = null;
        dom.modal.hidden = true;
        dom.modal.setAttribute('aria-hidden', 'true');

        if (state.reasonModalOpen) {
            state.reasonModalOpen = false;
            dom.reasonOverlay.setAttribute('aria-hidden', 'true');
            dom.reasonOverlay.hidden = true;
            dom.reasonForm.reset();
            state.reasonLastFocusedElement = null;
        }
        setParentDialogSuppressed(false);

        setBodyModalState();

        if (state.modalLastFocusedElement instanceof HTMLElement) {
            state.modalLastFocusedElement.focus();
        }
        state.modalLastFocusedElement = null;
    }

    function showReasonModal() {
        state.reasonModalOpen = true;
        state.reasonLastFocusedElement = document.activeElement;
        setParentDialogSuppressed(true);
        dom.reasonOverlay.hidden = false;
        dom.reasonOverlay.setAttribute('aria-hidden', 'false');
        dom.reasonInput.value = '';
        requestAnimationFrame(() => dom.reasonInput.focus());
    }

    function hideReasonModal() {
        if (!state.reasonModalOpen) {
            return;
        }

        state.reasonModalOpen = false;
        dom.reasonOverlay.setAttribute('aria-hidden', 'true');
        dom.reasonOverlay.hidden = true;
        setParentDialogSuppressed(false);
        dom.reasonForm.reset();
        state.pendingUser = null;

        if (state.reasonLastFocusedElement instanceof HTMLElement) {
            state.reasonLastFocusedElement.focus();
        }
        state.reasonLastFocusedElement = null;
    }

    function handleUserOptionClick(user) {
        state.pendingUser = user;
        closeDropdown();
        showReasonModal();
    }

    function renderChatListPlaceholder(message) {
        dom.chatList.innerHTML = '';
        const placeholder = document.createElement('div');
        placeholder.className = 'chat-list-placeholder';
        placeholder.textContent = message;
        dom.chatList.appendChild(placeholder);
    }

    function renderMessagesPlaceholder(message) {
        dom.messagesHeader.textContent = text('select_conversation_header', 'selectConversation');
        dom.messagesArea.innerHTML = '';

        const placeholder = document.createElement('div');
        placeholder.className = 'empty-state';
        placeholder.innerHTML = `
            <div class="empty-state-icon">
                ${Icons.chatFilesChooseChats}
            </div>
            <p class="empty-state-text"></p>
        `;
        placeholder.querySelector('.empty-state-text').textContent = message;
        dom.messagesArea.appendChild(placeholder);
    }

    function refreshLocalizedEmptyState() {
        renderUserSelection();
        renderUserDropdown({ loading: !state.usersLoaded });
        if (!state.selectedUser) {
            renderChatListPlaceholder(text('select_user_to_view', 'selectUserToView'));
            renderMessagesPlaceholder(text('select_user_to_begin', 'selectUserToBegin'));
        }
    }

    function renderChatList() {
        dom.chatList.innerHTML = '';

        if (!state.chats.length) {
            renderChatListPlaceholder(text('no_conversations_found', 'noConversations'));
            return;
        }

        const fragment = document.createDocumentFragment();
        state.chats.forEach((chat) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'chat-item';
            button.dataset.chatId = chat.id;
            if (state.selectedChatId === chat.id) {
                button.classList.add('active');
            }

            const title = document.createElement('div');
            title.className = 'chat-title';
            title.textContent = chat.title || text('untitled', 'untitled');
            button.appendChild(title);

            const meta = document.createElement('div');
            meta.className = 'chat-meta';
            meta.textContent = chat.last_updated_at
                ? formatDateTime(chat.last_updated_at)
                : text('unknown_date', 'unknownDate');
            button.appendChild(meta);

            button.addEventListener('click', () => handleChatSelect(chat));
            fragment.appendChild(button);
        });

        dom.chatList.appendChild(fragment);
    }

    function stringifyBlockContent(value) {
        if (value === null || value === undefined) {
            return '';
        }
        if (typeof value === 'string') {
            return value;
        }
        if (typeof value === 'number' || typeof value === 'boolean') {
            return String(value);
        }
        try {
            return JSON.stringify(value, null, 2);
        } catch (_) {
            return '';
        }
    }

    function formatTemplate(key, fallbackKey, vars) {
        if (typeof window.adminFormatT === 'function') {
            return window.adminFormatT(key, FALLBACKS[fallbackKey] || fallbackKey || key, vars);
        }
        return text(key, fallbackKey).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function extractBlockText(block) {
        if (!block || typeof block !== 'object') {
            return '';
        }

        const type = (block.type || '').toLowerCase();
        const content = stringifyBlockContent(block.content);
        const meta = block.meta || {};

        if (type === 'user' || type === 'content') {
            return content;
        }

        if (type === 'reasoning') {
            return content ? `${text('reasoning_label', 'reasoning')}\n${content}` : '';
        }

        if (type === 'tool_call') {
            const toolName = meta.tool_name || meta.name;
            // Canonical persisted calls use `arguments`; imported archives may
            // still contain either of the supported backend aliases.
            const rawArgs = meta.arguments ?? meta.tool_args ?? meta.args;
            const args = stringifyBlockContent(rawArgs);
            const parts = [];
            parts.push(toolName
                ? formatTemplate('tool_call_label_named', 'toolCallPrefix', { name: toolName })
                : text('tool_call_label', 'toolCall'));
            if (content) {
                parts.push(content);
            }
            if (args) {
                parts.push(formatTemplate('tool_call_args_label', 'argsPrefix', { args }));
            }
            return parts.join('\n');
        }

        if (type === 'widget') {
            return content ? `${text('widget_output_label', 'widgetOutput')}\n${content}` : '';
        }

        if (type === 'file') {
            const fileName = meta.file_name || meta.fileId || content;
            return fileName
                ? formatTemplate('file_attachment_label_named', 'fileAttachmentNamed', { name: fileName })
                : text('file_attachment_label', 'fileAttachment');
        }

        return content;
    }

    function normalizeMessageContent(message) {
        if (!message) {
            return '';
        }

        const parts = [];
        if (Array.isArray(message.content)) {
            message.content.forEach((block) => {
                const blockText = extractBlockText(block);
                if (blockText) {
                    parts.push(blockText);
                }
            });
        } else if (message.content) {
            parts.push(stringifyBlockContent(message.content));
        }

        if (message.thinking) {
            parts.push(`${text('reasoning_label', 'reasoning')}\n${stringifyBlockContent(message.thinking)}`);
        }

        return parts.join('\n\n').trim();
    }

    function formatAttachmentSummary(message) {
        if (!message || typeof message !== 'object') {
            return [];
        }

        const summary = [];
        const attachmentKeys = [
            { key: 'images', labelKey: 'attachment_images_label', fallbackKey: 'attachmentImages' },
            { key: 'videos', labelKey: 'attachment_videos_label', fallbackKey: 'attachmentVideos' },
            { key: 'audios', labelKey: 'attachment_audio_label', fallbackKey: 'attachmentAudio' },
            { key: 'documents', labelKey: 'attachment_documents_label', fallbackKey: 'attachmentDocuments' },
        ];

        attachmentKeys.forEach(({ key, labelKey, fallbackKey }) => {
            const items = message[key];
            if (Array.isArray(items) && items.length) {
                summary.push(formatTemplate(labelKey, fallbackKey, { count: items.length }));
            }
        });

        return summary;
    }

    function renderMessages(messages) {
        dom.messagesArea.innerHTML = '';

        if (!messages.length) {
            renderMessagesPlaceholder(text('no_messages_found', 'noMessages'));
            return;
        }

        const fragment = document.createDocumentFragment();
        messages.forEach((message) => {
            const role = (message.role || '').toLowerCase();
            const sender = message.name?.trim()
                || (role === 'assistant' ? text('assistant_label', 'assistant') : text('user_label', 'user'));
            const createdAt = message.created_at ? formatDateTime(message.created_at) : '';

            const wrapperEl = document.createElement('div');
            wrapperEl.className = `message ${role === 'user' ? 'user' : 'assistant'}`;

            const senderEl = document.createElement('div');
            senderEl.className = 'message-sender';
            senderEl.textContent = createdAt ? `${sender} · ${createdAt}` : sender;
            wrapperEl.appendChild(senderEl);

            const contentEl = document.createElement('div');
            contentEl.className = 'message-content';
            contentEl.textContent = normalizeMessageContent(message) || text('no_content', 'noContent');
            wrapperEl.appendChild(contentEl);

            const attachmentSummary = formatAttachmentSummary(message);
            if (attachmentSummary.length) {
                const attachmentsEl = document.createElement('div');
                attachmentsEl.className = 'message-attachments';
                attachmentsEl.textContent = attachmentSummary.join(' · ');
                wrapperEl.appendChild(attachmentsEl);
            }

            fragment.appendChild(wrapperEl);
        });

        dom.messagesArea.appendChild(fragment);
    }

    function parseDateTime(value) {
        if (value === null || value === undefined) {
            return null;
        }

        if (value instanceof Date) {
            return Number.isNaN(value.getTime()) ? null : value;
        }

        if (typeof value === 'number') {
            const date = new Date(value);
            return Number.isNaN(date.getTime()) ? null : date;
        }

        const raw = String(value).trim();
        if (!raw) {
            return null;
        }

        const isoLike = raw.includes('T') ? raw : raw.replace(' ', 'T');
        const hasTimezone = /([+-]\d{2}:\d{2}|Z)$/i.test(isoLike);
        const normalized = hasTimezone ? isoLike : `${isoLike}Z`;
        const date = new Date(normalized);

        return Number.isNaN(date.getTime()) ? null : date;
    }

    function formatDateTime(value) {
        try {
            const date = parseDateTime(value);
            if (!date) {
                return '';
            }
            return date.toLocaleString(undefined, {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
            });
        } catch (_) {
            return '';
        }
    }

    async function fetchChats() {
        if (!state.selectedUser || !state.reason) {
            return;
        }

        renderChatListPlaceholder(text('loading_label', 'loading'));
        renderMessagesPlaceholder(text('select_conversation_to_view', 'selectConversationToView'));

        try {
            const response = await window.authedFetch(API.chats, {
                method: 'POST',
                body: JSON.stringify({
                    user_id: state.selectedUser.id,
                    reason: state.reason,
                }),
            });

            if (!response.ok) {
                throw new Error(text('failed_to_fetch_chats', 'failedChatsFetch'));
            }

            const chats = await response.json();
            state.chats = Array.isArray(chats) ? chats : [];
            state.selectedChatId = null;
            renderChatList();
        } catch (error) {
            console.error('Failed to fetch user chats', error);
            renderChatListPlaceholder(text('failed_to_load', 'failedToLoad'));
            notify(error?.message || text('failed_to_fetch_chats', 'failedChatsFetch'));
        }
    }

    async function fetchChatMessages(chat) {
        if (!state.selectedUser || !chat || !state.reason) {
            return;
        }

        dom.messagesHeader.textContent = chat.title || text('conversation_label', 'conversation');
        dom.messagesArea.innerHTML = `<div class="messages-loading">${text('loading_label', 'loading')}</div>`;

        try {
            const response = await window.authedFetch(API.chatMessages, {
                method: 'POST',
                body: JSON.stringify({
                    user_id: state.selectedUser.id,
                    chat_id: chat.id,
                    reason: state.reason,
                }),
            });

            if (!response.ok) {
                throw new Error(text('failed_to_fetch_messages', 'failedMessagesFetch'));
            }

            const messages = await response.json();
            renderMessages(Array.isArray(messages) ? messages : []);
        } catch (error) {
            console.error('Failed to fetch chat messages', error);
            renderMessagesPlaceholder(text('failed_to_load_messages', 'failedToLoadMessages'));
            notify(error?.message || text('failed_to_fetch_messages', 'failedMessagesFetch'));
        }
    }

    function handleChatSelect(chat) {
        if (!chat || state.selectedChatId === chat.id) {
            return;
        }

        state.selectedChatId = chat.id;
        renderChatList();
        closeMobileSidebar();
        fetchChatMessages(chat);
    }

    if (dom.openButton) {
        dom.openButton.addEventListener('click', (event) => {
            event.preventDefault();
            showModal();
        });
    }

    dom.closeButton?.addEventListener('click', () => {
        hideModal();
    });

    dom.sidebarToggle?.addEventListener('click', () => {
        toggleMobileSidebar();
    });

    dom.sidebarScrim?.addEventListener('click', () => {
        closeMobileSidebar({ restoreFocus: true });
    });

    dom.modal?.addEventListener('click', (event) => {
        if (event.target === dom.modal) {
            hideModal();
        }
    });

    dom.trigger?.addEventListener('click', async () => {
        if (!state.usersLoaded) {
            await fetchUsers();
        }
        toggleDropdown();
    });

    dom.reasonCancelButton?.addEventListener('click', () => {
        hideReasonModal();
    });

    dom.reasonOverlay?.addEventListener('click', (event) => {
        if (event.target === dom.reasonOverlay) {
            hideReasonModal();
        }
    });

    dom.reasonForm?.addEventListener('submit', (event) => {
        event.preventDefault();

        const MAX_REASON_LENGTH = 256;
        const rawReason = dom.reasonInput.value || '';
        const reason = rawReason.trim();
        if (reason.length < 3) {
            dom.reasonInput.focus();
            dom.reasonInput.setSelectionRange(0, dom.reasonInput.value.length);
            return;
        }

        if (reason.length > MAX_REASON_LENGTH) {
            dom.reasonInput.focus();
            dom.reasonInput.setSelectionRange(0, dom.reasonInput.value.length);
            return;
        }

        if (!state.pendingUser) {
            hideReasonModal();
            return;
        }

        state.selectedUser = state.pendingUser;
        state.pendingUser = null;
        state.reason = reason;
        renderUserSelection();
        hideReasonModal();
        fetchChats();
    });

    renderUserSelection();
    renderUserDropdown({ loading: true });
    renderChatListPlaceholder(text('select_user_to_view', 'selectUserToView'));
    renderMessagesPlaceholder(text('select_user_to_begin', 'selectUserToBegin'));
    document.addEventListener('i18n:updated', refreshLocalizedEmptyState);

    window.adminChatOversightShowModal = showModal;
    window.adminChatOversightHideModal = hideModal;
})();
