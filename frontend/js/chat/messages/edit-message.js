// ===== User Message Edit Mode =====
let activeUserMessageEditSession = null;
const USER_MESSAGE_EDIT_UPLOAD_LIMIT_BYTES = 100 * 1024 * 1024;
const USER_MESSAGE_EDIT_DICTATION_STOP_ICON = '<span class="dictation-stop-icon"></span>';

function generateUserMessageEditTempId() {
    return `edit-upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function getUserMessageEditVoiceIcon() {
    return document.getElementById('chatBoxVoiceButton')?.innerHTML
        || Icons.microphone;
}

function getUserMessageEditAttachmentHelpers() {
    return window.ChatAttachmentHelpers || {};
}

function normalizeUserMessageEditCategory(file) {
    const helpers = getUserMessageEditAttachmentHelpers();
    if (typeof helpers.normalizeAttachmentCategory === 'function') {
        return helpers.normalizeAttachmentCategory(file?.file_category, file?.mime_type || file?.file_type || '');
    }
    const mimeType = String(file?.mime_type || file?.file_type || '').toLowerCase();
    if (mimeType.startsWith('image/')) return 'image';
    if (mimeType.startsWith('audio/')) return 'audio';
    if (mimeType.startsWith('video/')) return 'video';
    return 'document';
}

function normalizeUserMessageEditFile(file) {
    if (!file) {
        return null;
    }
    const fileId = String(file.file_id || file.id || '').trim();
    if (!fileId) {
        return null;
    }
    const meta = file.meta && typeof file.meta === 'object' ? file.meta : {};
    const name = String(
        file.original_name
        || file.original_filename
        || meta.original_filename
        || file.file_name
        || file.name
        || fileId
    );
    const mimeType = String(file.mime_type || file.file_type || meta.mime_type || '').trim();
    const fileSize = Number(file.file_size ?? meta.file_size ?? file.size ?? 0) || 0;
    return {
        ...file,
        id: fileId,
        file_id: fileId,
        original_name: name,
        original_filename: name,
        file_name: file.file_name || name,
        mime_type: mimeType,
        file_type: mimeType || file.file_type || '',
        file_size: fileSize,
        file_category: file.file_category || normalizeUserMessageEditCategory(file),
        temporary: Boolean(file.temporary),
        isUploading: Boolean(file.isUploading),
        progress: Number(file.progress || 0) || 0,
    };
}

function normalizeUserMessageEditFiles(files) {
    if (!Array.isArray(files)) {
        return [];
    }
    const normalized = [];
    const seen = new Set();
    files.forEach((file) => {
        const nextFile = normalizeUserMessageEditFile(file);
        if (!nextFile || seen.has(nextFile.id)) {
            return;
        }
        seen.add(nextFile.id);
        normalized.push(nextFile);
    });
    return normalized;
}

function getUserMessageEditFileSignature(files) {
    return normalizeUserMessageEditFiles(files)
        .map((file) => `${file.id}:${file.file_category || normalizeUserMessageEditCategory(file)}`)
        .sort()
        .join('|');
}

function normalizeUserMessageEditChatReference(chat) {
    if (!chat) {
        return null;
    }
    const chatId = String(chat.chat_id || chat.id || '').trim();
    if (!chatId) {
        return null;
    }
    const fallbackTitle = getStreamText('chat_reference_untitled', 'Untitled chat');
    return {
        chat_id: chatId,
        id: chatId,
        title: chatTitleUtils.getChatDisplayTitle?.(chat, fallbackTitle) || fallbackTitle,
        last_updated_at: chat.last_updated_at || null,
        snippet: String(chat.snippet || '').trim(),
        message_count: Number(chat.message_count || 0) || 0,
        estimated_chars: Number(chat.estimated_chars || 0) || 0,
        source: chatTitleUtils.isAutomationChat?.(chat) ? 'automation' : chatTitleUtils.getChatSource?.(chat) || '',
    };
}

function normalizeUserMessageEditChatReferences(chats) {
    if (!Array.isArray(chats)) {
        return [];
    }
    const normalized = [];
    const seen = new Set();
    chats.forEach((chat) => {
        const nextChat = normalizeUserMessageEditChatReference(chat);
        if (!nextChat || seen.has(nextChat.chat_id)) {
            return;
        }
        seen.add(nextChat.chat_id);
        normalized.push(nextChat);
    });
    return normalized;
}

function getUserMessageEditChatReferenceSignature(chats) {
    return normalizeUserMessageEditChatReferences(chats)
        .map((chat) => chat.chat_id)
        .sort()
        .join('|');
}

function renderUserMessageTextContent(userMessageContent, content) {
    const nextContent = String(content ?? '');
    userMessageContent.setAttribute('data-raw-content', nextContent);
    const renderUserMarkdown = safeGetLocalStorageItem('render_user_messages_markdown');
    if (renderUserMarkdown === 'true' && typeof renderMarkdownContent === 'function') {
        renderMarkdownContent(userMessageContent, nextContent);
        scheduleUserMessageExpandableRefresh(userMessageContent.closest('.user-message-container'));
        return;
    }
    userMessageContent.innerHTML = '';
    userMessageContent.textContent = nextContent;
    userMessageContent.classList.remove('markdown-body');
    scheduleUserMessageExpandableRefresh(userMessageContent.closest('.user-message-container'));
}

function rerenderUserMessageFiles(messageId, files, columnWrapper) {
    if (!columnWrapper) {
        return;
    }
    Array.from(columnWrapper.children).forEach((child) => {
        if (child instanceof HTMLElement && child.classList.contains('inline-files')) {
            child.remove();
        }
    });
    appendUserFiles(messageId, files, columnWrapper);
}

function buildUserMessageEditPayload(session, textOverride) {
    const normalizedFiles = normalizeUserMessageEditFiles(session.currentFiles);
    const backendMessageId = getUserMessageBackendId(session.messageId, session.userMessageContainer);
    const payload = {
        message_id: backendMessageId,
        content: typeof textOverride === 'string' ? textOverride : session.textarea.value.trim(),
        image_ids: [],
        video_ids: [],
        audio_ids: [],
        document_ids: [],
        chat_reference_ids: normalizeUserMessageEditChatReferences(session.currentChatReferences).map((chat) => chat.chat_id),
    };
    normalizedFiles.forEach((file) => {
        switch (normalizeUserMessageEditCategory(file)) {
            case 'image':
                payload.image_ids.push(file.id);
                break;
            case 'video':
                payload.video_ids.push(file.id);
                break;
            case 'audio':
                payload.audio_ids.push(file.id);
                break;
            case 'document':
            default:
                payload.document_ids.push(file.id);
                break;
        }
    });
    return payload;
}

function userMessageEditHasChanges(session) {
    const currentText = session.textarea?.value?.trim() || '';
    return currentText !== session.originalText
        || getUserMessageEditFileSignature(session.currentFiles) !== session.originalFilesSignature
        || getUserMessageEditChatReferenceSignature(session.currentChatReferences) !== session.originalChatReferencesSignature;
}

function updateUserMessageEditActionState(session) {
    if (!session || !session.textarea) {
        return;
    }
    const hasUploading = session.pendingUploads.size > 0;
    const hasText = Boolean(session.textarea.value.trim());
    const hasChanges = userMessageEditHasChanges(session);
    const disableActions = session.isSaving;

    if (session.textarea) {
        session.textarea.disabled = Boolean(session.isSaving);
    }
    if (session.uploadButton) {
        session.uploadButton.disabled = disableActions;
        session.uploadButton.setAttribute('aria-disabled', disableActions ? 'true' : 'false');
    }
    if (session.voiceButton) {
        session.voiceButton.disabled = Boolean(session.isSaving || !session.dictationSupported);
    }
    if (session.cancelButton) {
        session.cancelButton.disabled = Boolean(session.isSaving);
    }
    if (session.saveButton) {
        const saveDisabled = Boolean(disableActions || hasUploading || !hasText || !hasChanges);
        session.saveButton.disabled = saveDisabled;
        session.saveButton.textContent = session.isSaving
            ? getStreamText('common_saving', 'Saving...')
            : getStreamText('common_save', 'Save');
        if (session.saveMenuButton) {
            session.saveMenuButton.disabled = saveDisabled;
            session.saveMenuButton.setAttribute('aria-disabled', saveDisabled ? 'true' : 'false');
        }
        if (session.saveAndRegenerateButton) {
            session.saveAndRegenerateButton.disabled = saveDisabled;
        }
        if (saveDisabled) {
            session.closeSaveDropdown?.();
        }
    }
}

/**
 * Return whether the current user's group explicitly allows response
 * regeneration. The live setup response is authoritative; deliberately do not
 * fall back to localStorage because it can still contain a previous group's
 * permission after an account or group change.
 */
function isUserMessageEditRegenerationAllowed() {
    return Boolean(
        typeof window !== 'undefined'
        && window.chatSetup
        && Object.prototype.hasOwnProperty.call(window.chatSetup, 'allow_regenerate_response')
        && window.chatSetup.allow_regenerate_response === true
    );
}

/**
 * Resolve the one assistant response that the backend permits regenerating for
 * this edit session. Reusing the response toolbar's eligibility checks keeps
 * retry limits, streaming state, and version state consistent in both places.
 */
function getUserMessageEditRegenerationTarget(session) {
    if (!session?.userMessageContainer || !isUserMessageEditRegenerationAllowed()) {
        return null;
    }

    const chatAreaContainer = document.getElementById('chatAreaContainer');
    if (!chatAreaContainer) {
        return null;
    }

    const userMessages = Array.from(chatAreaContainer.querySelectorAll('.user-message-container'));
    if (!userMessages.length || userMessages[userMessages.length - 1] !== session.userMessageContainer) {
        return null;
    }

    const assistantMessages = Array.from(chatAreaContainer.querySelectorAll('.assistant-message-container'));
    const assistantContainer = assistantMessages[assistantMessages.length - 1] || null;
    if (!assistantContainer || !canRegenerateAssistantMessage(assistantContainer)) {
        return null;
    }

    const linkedUserMessage = getAssistantRegenerateUserMessageTarget(assistantContainer);
    if (linkedUserMessage?.userMessageContainer !== session.userMessageContainer) {
        return null;
    }

    const assistantMessageId = String(assistantContainer.id || '').replace(/^a-/, '').trim();
    if (!assistantMessageId) {
        return null;
    }

    return { assistantContainer, assistantMessageId };
}

function autoResizeUserMessageEditTextarea(textarea) {
    if (!textarea) {
        return;
    }
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 24), 320)}px`;
}

function setUserMessageEditExternalFilesHidden(session, hidden) {
    if (!session?.columnWrapper) {
        return;
    }
    const siblings = Array.from(session.columnWrapper.children).filter((child) => (
        child instanceof HTMLElement
        && child.classList.contains('inline-files')
        && child !== session.editContainer
    ));
    siblings.forEach((element) => {
        if (hidden) {
            element.dataset.editPreviousDisplay = element.style.display || '';
            element.style.display = 'none';
        } else {
            element.style.display = element.dataset.editPreviousDisplay || '';
            delete element.dataset.editPreviousDisplay;
        }
    });
}

function createUserMessageEditAttachmentTile(session, file) {
    const helpers = getUserMessageEditAttachmentHelpers();
    const attachmentName = file.original_name || file.file_name || file.id;
    const extension = typeof helpers.getFileExtensionLabel === 'function'
        ? helpers.getFileExtensionLabel(attachmentName)
        : 'FILE';
    const iconName = typeof helpers.getFileIconName === 'function'
        ? helpers.getFileIconName(file.mime_type || file.file_type)
        : 'txt.svg';

    const element = document.createElement('div');
    element.className = 'inline-files-element';
    element.dataset.fileId = file.id;
    if (file.isUploading) {
        element.classList.add('is-uploading');
    }

    const iconWrapper = document.createElement('div');
    iconWrapper.className = 'inline-files-element-icon';
    const iconImg = document.createElement('img');
    // Root-relative paths continue to work when a chat uses a nested route.
    iconImg.src = `/assets/file_svgs/${iconName}`;
    iconImg.alt = extension;
    iconImg.width = 28;
    iconImg.height = 28;
    iconWrapper.appendChild(iconImg);

    const content = document.createElement('div');
    content.className = 'inline-files-element-content';

    const topRow = document.createElement('div');
    topRow.className = 'inline-files-element-content-top';
    const nameEl = document.createElement('p');
    nameEl.textContent = attachmentName;
    topRow.appendChild(nameEl);

    const bottomRow = document.createElement('div');
    bottomRow.className = 'inline-files-element-content-bottom';
    const extensionEl = document.createElement('p');
    extensionEl.textContent = extension;
    bottomRow.appendChild(extensionEl);

    const formatBytes = typeof helpers.formatBytes === 'function'
        ? helpers.formatBytes
        : ((bytes) => `${Math.round(bytes || 0)} B`);
    if (file.file_size) {
        const sizeEl = document.createElement('p');
        sizeEl.textContent = formatBytes(file.file_size);
        bottomRow.appendChild(sizeEl);
    } else if (file.isUploading) {
        const statusEl = document.createElement('p');
        statusEl.textContent = `${Math.round(file.progress || 0)}%`;
        bottomRow.appendChild(statusEl);
    }

    content.appendChild(topRow);
    content.appendChild(bottomRow);

    const deleteEl = document.createElement('div');
    deleteEl.className = 'inline-files-element-delete';
    deleteEl.setAttribute('role', 'button');
    deleteEl.setAttribute('tabindex', '0');
    const removeLabel = getStreamTextFormatted(
        'chat_sr_remove_attachment',
        'Remove attachment: {name}',
        { name: attachmentName },
    );
    deleteEl.setAttribute('aria-label', removeLabel);
    deleteEl.title = removeLabel;
    deleteEl.innerHTML = Icons.close;
    const removeAttachment = () => removeUserMessageEditAttachment(session, file.id);
    deleteEl.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        removeAttachment();
    });
    deleteEl.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            removeAttachment();
        }
    });

    element.appendChild(iconWrapper);
    element.appendChild(content);
    element.appendChild(deleteEl);

    if (file.isUploading) {
        const overlay = document.createElement('div');
        overlay.className = 'inline-files-upload';
        const bar = document.createElement('div');
        bar.className = 'inline-files-upload-bar';
        const fill = document.createElement('div');
        fill.className = 'inline-files-upload-fill';
        fill.style.width = `${Math.max(0, Math.min(100, file.progress || 0))}%`;
        overlay.appendChild(bar);
        overlay.appendChild(fill);
        element.appendChild(overlay);
    }

    return element;
}

function createUserMessageEditChatReferenceTile(session, chat) {
    const chatId = String(chat?.chat_id || chat?.id || '').trim();
    const fallbackTitle = getStreamText('chat_reference_untitled', 'Untitled chat');
    const title = chatTitleUtils.getChatDisplayTitle?.(chat, fallbackTitle) || fallbackTitle;

    const element = document.createElement('div');
    element.className = 'inline-files-element inline-chat-reference-element';
    element.dataset.chatReferenceId = chatId;

    const iconEl = document.createElement('span');
    iconEl.className = 'inline-skill-element-icon inline-chat-reference-element-icon';
    iconEl.innerHTML = Icons.chatFilesChooseChats;

    const content = document.createElement('div');
    content.className = 'inline-files-element-content';
    const topRow = document.createElement('div');
    topRow.className = 'inline-files-element-content-top';
    const titleEl = document.createElement('p');
    titleEl.className = 'chat-title-with-badge';
    if (typeof chatTitleUtils.setChatTitleElement === 'function') {
        chatTitleUtils.setChatTitleElement(titleEl, chat, { fallbackTitle });
    } else {
        titleEl.textContent = title;
        titleEl.title = title;
    }
    topRow.appendChild(titleEl);

    const bottomRow = document.createElement('div');
    bottomRow.className = 'inline-files-element-content-bottom';
    const metaEl = document.createElement('p');
    metaEl.textContent = String(chat?.snippet || '').trim() || getStreamText('chat_reference_attached_context', 'Attached chat context');
    bottomRow.appendChild(metaEl);
    content.appendChild(topRow);
    content.appendChild(bottomRow);

    const deleteEl = document.createElement('div');
    deleteEl.className = 'inline-files-element-delete';
    deleteEl.setAttribute('role', 'button');
    deleteEl.setAttribute('tabindex', '0');
    deleteEl.setAttribute('aria-label', getStreamText('chat_reference_remove_chat_aria', 'Remove chat reference'));
    deleteEl.innerHTML = Icons.close;
    const removeReference = () => removeUserMessageEditChatReference(session, chatId);
    deleteEl.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        removeReference();
    });
    deleteEl.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            removeReference();
        }
    });

    element.appendChild(iconEl);
    element.appendChild(content);
    element.appendChild(deleteEl);
    return element;
}

function renderUserMessageEditAttachments(session) {
    if (!session?.attachmentsContainer) {
        return;
    }
    session.currentFiles = normalizeUserMessageEditFiles(session.currentFiles);
    session.currentChatReferences = normalizeUserMessageEditChatReferences(session.currentChatReferences);
    session.attachmentsContainer.innerHTML = '';
    if (!session.currentFiles.length && !session.currentChatReferences.length) {
        session.attachmentsContainer.classList.remove('active');
        session.attachmentsContainer.hidden = true;
        renderUserMessageEditFilesQuickpick(session);
        renderUserMessageEditChatReferencesQuickpick(session);
        updateUserMessageEditActionState(session);
        return;
    }
    const fragment = document.createDocumentFragment();
    session.currentChatReferences.forEach((chat) => {
        fragment.appendChild(createUserMessageEditChatReferenceTile(session, chat));
    });
    session.currentFiles.forEach((file) => {
        fragment.appendChild(createUserMessageEditAttachmentTile(session, file));
    });
    session.attachmentsContainer.appendChild(fragment);
    session.attachmentsContainer.hidden = false;
    session.attachmentsContainer.classList.add('active');
    renderUserMessageEditFilesQuickpick(session);
    renderUserMessageEditChatReferencesQuickpick(session);
    updateUserMessageEditActionState(session);
}

function removeUserMessageEditAttachment(session, fileId) {
    if (!session || !fileId) {
        return;
    }
    const pending = session.pendingUploads.get(fileId);
    if (pending) {
        try {
            pending.abort?.();
        } catch (_) {}
        session.pendingUploads.delete(fileId);
    }
    session.currentFiles = session.currentFiles.filter((file) => file.id !== fileId);
    renderUserMessageEditAttachments(session);
}

function removeUserMessageEditChatReference(session, chatId) {
    if (!session || !chatId) {
        return;
    }
    const normalizedId = String(chatId).trim();
    session.currentChatReferences = normalizeUserMessageEditChatReferences(session.currentChatReferences)
        .filter((chat) => chat.chat_id !== normalizedId);
    renderUserMessageEditAttachments(session);
}

const USER_MESSAGE_EDIT_FILES_PAGE_LIMIT = 50;
const USER_MESSAGE_EDIT_FILES_LOAD_MORE_THRESHOLD = 120;
const USER_MESSAGE_EDIT_FILES_SEARCH_DEBOUNCE_MS = 250;

function normalizeUserMessageEditFilesPage(payload, fallbackOptions = {}) {
    if (Array.isArray(payload)) {
        const limit = Number(fallbackOptions.limit || payload.length || USER_MESSAGE_EDIT_FILES_PAGE_LIMIT);
        const offset = Number(fallbackOptions.offset || 0);
        return {
            items: payload,
            total: offset + payload.length,
            limit,
            offset,
            hasMore: payload.length >= limit,
        };
    }
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const limit = Number(payload?.limit ?? fallbackOptions.limit ?? USER_MESSAGE_EDIT_FILES_PAGE_LIMIT);
    const offset = Number(payload?.offset ?? fallbackOptions.offset ?? 0);
    return {
        items,
        total: Number(payload?.total ?? offset + items.length),
        limit,
        offset,
        hasMore: Boolean(payload?.has_more),
    };
}

async function fetchUserMessageEditUploadedFilesPage(options = {}) {
    if (typeof window.fetchFilesPage === 'function') {
        return normalizeUserMessageEditFilesPage(await window.fetchFilesPage(options), options);
    }

    const params = new URLSearchParams();
    const search = String(options.search || '').trim();
    if (search) {
        params.set('search', search);
    }
    params.set('sort_field', options.sortField || 'created_at');
    params.set('sort_direction', options.sortDirection || 'desc');
    params.set('limit', String(options.limit || USER_MESSAGE_EDIT_FILES_PAGE_LIMIT));
    params.set('offset', String(options.offset || 0));

    const response = await window.authedFetch(`/api/v1/files/workspace?${params.toString()}`, { method: 'GET' });
    if (!response.ok) {
        throw new Error(getStreamTextFormatted('chat_files_fetch_failed_status', 'Failed to fetch files ({status})', {
            status: response.status,
        }));
    }
    return normalizeUserMessageEditFilesPage(await response.json(), options);
}

function mergeUserMessageEditFilePages(existing, incoming) {
    const merged = Array.isArray(existing) ? [...existing] : [];
    (Array.isArray(incoming) ? incoming : []).forEach((file) => {
        const fileId = String(file?.file_id ?? file?.id ?? '').trim();
        if (!fileId) return;
        const index = merged.findIndex((entry) => String(entry?.file_id ?? entry?.id ?? '') === fileId);
        if (index >= 0) {
            merged.splice(index, 1, file);
        } else {
            merged.push(file);
        }
    });
    return merged;
}

async function loadUserMessageEditQuickpickFiles(session, { append = false } = {}) {
    const picker = session?.editQuickpick?.files;
    if (!picker || !picker.list || !picker.empty || !picker.scrollRegion) {
        return;
    }
    if (picker.loading && append) {
        return;
    }

    const requestId = (picker.requestId || 0) + 1;
    picker.requestId = requestId;
    picker.loading = true;
    const search = String(picker.searchInput?.value || '').trim();
    const offset = append ? picker.items.length : 0;
    picker.scrollRegion?.setAttribute('aria-busy', 'true');

    if (!append) {
        picker.items = [];
        picker.hasMore = false;
        picker.scrollRegion.scrollTop = 0;
        picker.list.replaceChildren();
        picker.empty.hidden = false;
        picker.empty.textContent = getChatA11yText('files_loading', 'Loading files...');
    }

    try {
        const page = await fetchUserMessageEditUploadedFilesPage({
            search,
            offset,
            limit: USER_MESSAGE_EDIT_FILES_PAGE_LIMIT,
            sortField: 'created_at',
            sortDirection: 'desc',
        });
        if (picker.requestId !== requestId) {
            return;
        }
        picker.items = append ? mergeUserMessageEditFilePages(picker.items, page.items) : page.items;
        picker.hasMore = page.hasMore;
        picker.total = page.total;
        renderUserMessageEditFilesQuickpick(session);
        requestAnimationFrame(() => maybeLoadMoreUserMessageEditFiles(session));
    } catch (error) {
        console.error('Failed to load files for message edit quick-pick', error);
        if (!append) {
            picker.items = [];
            picker.empty.hidden = false;
            picker.empty.textContent = error.message || getChatA11yText('files_error_load', 'Failed to load files');
        }
    } finally {
        if (picker.requestId === requestId) {
            picker.loading = false;
            picker.scrollRegion?.setAttribute('aria-busy', 'false');
        }
    }
}

function createUserMessageEditQuickpickCheckbox(checked) {
    return window.ChatFilesMenu.createQuickpickCheckbox(checked);
}

function renderUserMessageEditFilesQuickpick(session) {
    const picker = session?.editQuickpick?.files;
    if (!picker || !picker.list || !picker.empty || !picker.scrollRegion) {
        return;
    }
    const query = String(picker.searchInput?.value || '').trim().toLowerCase();
    picker.list.replaceChildren();

    const selectedIds = new Set(normalizeUserMessageEditFiles(session.currentFiles).map((file) => file.id));
    const candidates = (Array.isArray(picker.items) ? picker.items : [])
        .map((file) => normalizeUserMessageEditFile(file))
        .filter(Boolean)
        .filter((file) => !query || String(file.original_name || file.file_name || '').toLowerCase().includes(query))
        .filter((file) => typeof window.isChatFileSupportedForCurrentModel !== 'function' || window.isChatFileSupportedForCurrentModel(file));

    if (!candidates.length) {
        picker.empty.hidden = false;
        picker.empty.textContent = picker.loading
            ? getChatA11yText('files_loading', 'Loading files...')
            : query
                ? getStreamText('chat_files_quickpick_no_matching_files', 'No matching files')
                : getStreamText('chat_files_quickpick_empty_files', 'No files found');
        return;
    }
    picker.empty.hidden = true;

    const fragment = document.createDocumentFragment();
    candidates.forEach((file) => {
        const item = document.createElement('label');
        item.className = `chatbox-files-quickpick__item${selectedIds.has(file.id) ? ' is-selected' : ''}`;
        item.dataset.fileId = file.id;

        const iconWrapper = document.createElement('span');
        iconWrapper.className = 'chatbox-files-quickpick__item-icon';
        const icon = document.createElement('img');
        const helpers = getUserMessageEditAttachmentHelpers();
        const iconName = typeof helpers.getFileIconName === 'function'
            ? helpers.getFileIconName(file.mime_type || file.file_type)
            : 'txt.svg';
        icon.src = `/assets/file_svgs/${iconName}`;
        icon.alt = '';
        icon.width = 20;
        icon.height = 20;
        iconWrapper.appendChild(icon);

        const name = document.createElement('span');
        name.className = 'chatbox-files-quickpick__item-name';
        name.textContent = file.original_name || file.file_name || file.id;
        const checkbox = createUserMessageEditQuickpickCheckbox(selectedIds.has(file.id));
        item.append(iconWrapper, name, checkbox);

        checkbox.addEventListener('change', (event) => {
            event.stopPropagation();
            const currentFiles = normalizeUserMessageEditFiles(session.currentFiles);
            if (!checkbox.checked) {
                session.currentFiles = currentFiles.filter((entry) => entry.id !== file.id);
            } else {
                session.currentFiles = normalizeUserMessageEditFiles([...currentFiles, file]);
            }
            renderUserMessageEditAttachments(session);
        });

        fragment.appendChild(item);
    });

    picker.list.appendChild(fragment);
    requestAnimationFrame(() => maybeLoadMoreUserMessageEditFiles(session));
}

function maybeLoadMoreUserMessageEditFiles(session) {
    const picker = session?.editQuickpick?.files;
    const scroller = picker?.scrollRegion;
    if (!scroller || picker.loading || !picker.hasMore || scroller.clientHeight <= 0) {
        return;
    }
    const nearBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - USER_MESSAGE_EDIT_FILES_LOAD_MORE_THRESHOLD;
    if (nearBottom) {
        void loadUserMessageEditQuickpickFiles(session, { append: true });
    }
}

async function fetchUserMessageEditChatReferences(options = {}) {
    if (typeof window.fetchChatReferenceCandidates === 'function') {
        return window.fetchChatReferenceCandidates({
            query: options.query || '',
            offset: options.offset || 0,
            limit: options.limit || 50,
            forceRefresh: Boolean(options.forceRefresh),
        });
    }
    const params = new URLSearchParams();
    const query = String(options.query || '').trim();
    params.set('limit', String(options.limit || 50));
    params.set('offset', String(options.offset || 0));
    if (query) {
        params.set('q', query);
    }
    const chatContainerEl = document.getElementById('chatContainer');
    const projectId = String(chatContainerEl?.getAttribute('data-project-id') || '').trim();
    if (projectId) {
        params.set('project_id', projectId);
    }
    const response = await window.authedFetch(`/api/v1/chats/references?${params.toString()}`, { method: 'GET' });
    if (!response.ok) {
        throw new Error(getStreamTextFormatted('chat_files_fetch_failed_status', 'Failed to fetch files ({status})', {
            status: response.status,
        }));
    }
    return response.json();
}

function mergeUserMessageEditChatReferences(existing, incoming) {
    const byId = new Map();
    [...(Array.isArray(existing) ? existing : []), ...(Array.isArray(incoming) ? incoming : [])].forEach((chat) => {
        const normalized = normalizeUserMessageEditChatReference(chat);
        if (normalized) {
            byId.set(normalized.chat_id, chat);
        }
    });
    return Array.from(byId.values());
}

async function loadUserMessageEditChatReferences(session, { forceRefresh = false, append = false } = {}) {
    const picker = session?.editQuickpick?.chats;
    if (!picker || !picker.list || !picker.empty || !picker.scrollRegion) {
        return;
    }
    if (append && (picker.loading || !picker.hasMore)) {
        return;
    }
    const query = String(picker.searchInput?.value || '').trim();
    const requestId = (picker.requestId || 0) + 1;
    picker.requestId = requestId;
    picker.loading = true;
    picker.scrollRegion?.setAttribute('aria-busy', 'true');
    if (!append) {
        picker.items = [];
        picker.hasMore = false;
        picker.list.replaceChildren();
        picker.empty.hidden = false;
        picker.empty.textContent = getStreamText('command_palette_loading_chats', 'Searching chats…');
        picker.scrollRegion.scrollTop = 0;
    }
    try {
        const result = await fetchUserMessageEditChatReferences({
            query,
            offset: append ? picker.items.length : 0,
            limit: 50,
            forceRefresh,
        });
        if (picker.requestId !== requestId) {
            return;
        }
        picker.items = append
            ? mergeUserMessageEditChatReferences(picker.items, result?.items)
            : (Array.isArray(result?.items) ? result.items : []);
        picker.hasMore = Boolean(result?.has_more);
        picker.total = Number(result?.total_count || picker.items.length) || picker.items.length;
        renderUserMessageEditChatReferencesQuickpick(session);
    } catch (error) {
        console.error('Failed to load message edit chat references', error);
        if (!append) {
            picker.items = [];
            picker.empty.hidden = false;
            picker.empty.textContent = error.message || getStreamText('chat_files_quickpick_failed_chats', 'Failed to load chats');
        }
    } finally {
        if (picker.requestId === requestId) {
            picker.loading = false;
            picker.scrollRegion?.setAttribute('aria-busy', 'false');
            requestAnimationFrame(() => maybeLoadMoreUserMessageEditChatReferences(session));
        }
    }
}

function renderUserMessageEditChatReferencesQuickpick(session) {
    const picker = session?.editQuickpick?.chats;
    if (!picker || !picker.list || !picker.empty || !picker.scrollRegion) {
        return;
    }
    const currentChatId = String(document.getElementById('chatContainer')?.getAttribute('data-chat-id') || '').trim();
    const selectedIds = new Set(normalizeUserMessageEditChatReferences(session.currentChatReferences).map((chat) => chat.chat_id));
    const chats = (Array.isArray(picker.items) ? picker.items : [])
        .map((chat) => normalizeUserMessageEditChatReference(chat))
        .filter(Boolean)
        .filter((chat) => !currentChatId || chat.chat_id !== currentChatId);
    picker.list.replaceChildren();

    if (!chats.length) {
        picker.empty.hidden = false;
        picker.empty.textContent = picker.loading
            ? getStreamText('command_palette_loading_chats', 'Searching chats…')
            : String(picker.searchInput?.value || '').trim()
                ? getStreamText('chat_files_quickpick_no_matching_chats', 'No matching chats')
                : getStreamText('chat_files_quickpick_empty_chats', 'No chats found');
        return;
    }
    picker.empty.hidden = true;

    const fragment = document.createDocumentFragment();
    chats.forEach((chat) => {
        const item = document.createElement('label');
        item.className = `chatbox-files-quickpick__item${selectedIds.has(chat.chat_id) ? ' is-selected' : ''}`;
        item.dataset.chatReferenceId = chat.chat_id;

        const iconWrapper = document.createElement('span');
        iconWrapper.className = 'chatbox-files-quickpick__item-icon';
        iconWrapper.innerHTML = `<span class="inline-chat-reference-element-icon">${Icons.chatFilesChooseChats}</span>`;

        const name = document.createElement('span');
        name.className = 'chatbox-files-quickpick__item-name chat-title-with-badge';
        const fallbackTitle = getStreamText('chat_reference_untitled', 'Untitled chat');
        const title = chatTitleUtils.getChatDisplayTitle?.(chat, fallbackTitle) || fallbackTitle;
        if (typeof chatTitleUtils.setChatTitleElement === 'function') {
            chatTitleUtils.setChatTitleElement(name, chat, { fallbackTitle });
        } else {
            name.textContent = title;
            name.title = title;
        }
        name.title = String(chat.snippet || '').trim() || name.title || title;
        const checkbox = createUserMessageEditQuickpickCheckbox(selectedIds.has(chat.chat_id));
        item.append(iconWrapper, name, checkbox);

        checkbox.addEventListener('change', (event) => {
            event.stopPropagation();
            const current = normalizeUserMessageEditChatReferences(session.currentChatReferences);
            const isSelected = current.some((entry) => entry.chat_id === chat.chat_id);
            if (checkbox.checked && !isSelected && current.length >= 5) {
                checkbox.checked = false;
                notifyWarning?.(getStreamTextFormatted('chat_reference_attach_limit', 'You can attach up to {count} chats.', { count: 5 }));
                return;
            }
            if (!checkbox.checked) {
                session.currentChatReferences = current.filter((entry) => entry.chat_id !== chat.chat_id);
            } else {
                session.currentChatReferences = normalizeUserMessageEditChatReferences([...current, chat]);
            }
            renderUserMessageEditAttachments(session);
        });

        fragment.appendChild(item);
    });
    picker.list.appendChild(fragment);
}

function maybeLoadMoreUserMessageEditChatReferences(session) {
    const picker = session?.editQuickpick?.chats;
    const scroller = picker?.scrollRegion;
    if (!scroller || picker.loading || !picker.hasMore || scroller.clientHeight <= 0) {
        return;
    }
    if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - USER_MESSAGE_EDIT_FILES_LOAD_MORE_THRESHOLD) {
        void loadUserMessageEditChatReferences(session, { append: true });
    }
}

function updateUserMessageEditDictationButton(session, state) {
    if (!session?.voiceButton) {
        return;
    }
    session.voiceButton.classList.remove('is-recording', 'is-transcribing');
    switch (state) {
        case 'recording':
            session.voiceButton.classList.add('is-recording');
            session.voiceButton.innerHTML = USER_MESSAGE_EDIT_DICTATION_STOP_ICON;
            session.voiceButton.setAttribute('aria-label', getStreamText('chat_voice_stop_recording', 'Stop recording'));
            session.voiceButton.title = getStreamText('chat_voice_stop_recording', 'Stop recording');
            break;
        case 'transcribing':
            session.voiceButton.classList.add('is-transcribing');
            session.voiceButton.innerHTML = getUserMessageEditVoiceIcon();
            session.voiceButton.setAttribute('aria-label', getStreamText('chat_voice_cancel_transcription', 'Cancel transcription'));
            session.voiceButton.title = getStreamText('chat_voice_cancel_transcription', 'Cancel transcription');
            break;
        default:
            session.voiceButton.innerHTML = getUserMessageEditVoiceIcon();
            session.voiceButton.setAttribute('aria-label', getStreamText('chat_voice_input', 'Voice input'));
            session.voiceButton.title = getStreamText('chat_voice_input', 'Voice input');
            break;
    }
}

function insertUserMessageEditTextAtCursor(session, text) {
    if (!session?.textarea) {
        return;
    }
    const textarea = session.textarea;
    const start = textarea.selectionStart || 0;
    const end = textarea.selectionEnd || 0;
    const currentValue = textarea.value || '';
    let insertText = text;
    if (start > 0 && currentValue[start - 1] && !/\s/.test(currentValue[start - 1])) {
        insertText = ` ${insertText}`;
    }
    textarea.value = `${currentValue.slice(0, start)}${insertText}${currentValue.slice(end)}`;
    const nextCursor = start + insertText.length;
    textarea.setSelectionRange(nextCursor, nextCursor);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
}

function stopUserMessageEditDictationResources(session) {
    const dictation = session?.dictation;
    if (!dictation) {
        return;
    }
    if (dictation.abortController) {
        dictation.abortController.abort();
        dictation.abortController = null;
    }
    if (dictation.usesLiveTranscription) {
        window.LiveTranscription?.cancel?.();
        dictation.usesLiveTranscription = false;
    }
    if (dictation.stream) {
        dictation.stream.getTracks().forEach((track) => track.stop());
        dictation.stream = null;
    }
}

function applyUserMessageEditLiveTranscript(session, text) {
    const dictation = session?.dictation;
    const base = dictation?.liveBase;
    if (!session?.textarea || !base) {
        return;
    }
    const transcript = String(text || '').trim();
    let insertText = transcript;
    if (
        insertText
        && base.start > 0
        && base.value[base.start - 1]
        && !/\s/.test(base.value[base.start - 1])
    ) {
        insertText = ` ${insertText}`;
    }
    session.textarea.value = `${base.value.slice(0, base.start)}${insertText}${base.value.slice(base.end)}`;
    const cursor = base.start + insertText.length;
    session.textarea.setSelectionRange(cursor, cursor);
    session.textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

async function startUserMessageEditLiveDictation(session) {
    const dictation = session.dictation;
    dictation.liveBase = {
        value: session.textarea.value || '',
        start: session.textarea.selectionStart || 0,
        end: session.textarea.selectionEnd || 0,
    };
    dictation.usesLiveTranscription = true;
    session.textarea.readOnly = true;
    let errorHandled = false;
    let liveCaptureStarted = false;
    const handleError = (error) => {
        if (session.destroyed || errorHandled) return;
        errorHandled = true;
        const shouldFallbackToFile = Boolean(
            !liveCaptureStarted
            && dictation.fileTranscriptionReady
            && navigator.mediaDevices?.getUserMedia
            && window.MediaRecorder
            && window.LiveTranscription?.shouldFallbackToFile?.(error)
        );
        dictation.isRecording = false;
        dictation.isTranscribing = false;
        dictation.usesLiveTranscription = false;
        if (dictation.liveBase) {
            applyUserMessageEditLiveTranscript(session, '');
        }
        dictation.liveBase = null;
        session.textarea.readOnly = false;
        updateUserMessageEditDictationButton(session, 'idle');
        updateUserMessageEditActionState(session);
        if (shouldFallbackToFile) {
            // Bypass live exactly once so the normal toggle path starts the
            // configured completed-file recorder instead of retrying the
            // unavailable live transport recursively.
            dictation.skipLiveOnce = true;
            void toggleUserMessageEditDictation(session);
            return;
        }
        if (error?.isDictationRateLimit) {
            notifyWarning?.(getStreamText(
                'us_rate_limits_feature_exceeded_notice',
                'Minute limit reached. Try again after the reset time.',
            ));
        } else if (error?.isDictationInProgress) {
            notifyWarning?.(getStreamText(
                'chat_live_transcription_in_progress',
                'Another live transcription is already active. Stop it or wait a moment, then try again.',
            ));
        } else if (error?.isProviderRateLimit) {
            notifyWarning?.(getStreamText(
                'chat_live_transcription_provider_rate_limited',
                'The transcription provider is temporarily limiting requests. Please try again shortly.',
            ));
        } else {
            notifyError?.(getStreamText(
                'chat_live_transcription_failed',
                'Live transcription failed. Please try again.',
            ));
        }
    };

    try {
        await window.LiveTranscription.start({
            onPartial: (text) => {
                if (!session.destroyed) {
                    applyUserMessageEditLiveTranscript(session, text);
                }
            },
            onFinal: (text) => {
                if (session.destroyed) return;
                const transcript = String(text || '').trim();
                applyUserMessageEditLiveTranscript(session, transcript);
                dictation.isRecording = false;
                dictation.isTranscribing = false;
                dictation.usesLiveTranscription = false;
                dictation.liveBase = null;
                session.textarea.readOnly = false;
                updateUserMessageEditDictationButton(session, 'idle');
                updateUserMessageEditActionState(session);
                if (transcript) {
                    notifySuccess?.(getStreamText(
                        'chat_dictation_transcription_complete',
                        'Transcription complete.',
                    ));
                } else {
                    notifyWarning?.(getStreamText(
                        'chat_dictation_no_speech_detected',
                        'No speech detected in the recording.',
                    ));
                }
            },
            onError: handleError,
            onLimit: () => {
                notifyWarning?.(getStreamText(
                    'us_rate_limits_feature_exceeded_notice',
                    'Minute limit reached. Try again after the reset time.',
                ));
            },
            onStateChange: (state) => {
                if (session.destroyed || state !== 'transcribing') return;
                dictation.isRecording = false;
                dictation.isTranscribing = true;
                updateUserMessageEditDictationButton(session, 'transcribing');
                updateUserMessageEditActionState(session);
            },
        });
        if (session.destroyed) {
            window.LiveTranscription.cancel();
            return;
        }
        liveCaptureStarted = true;
        dictation.isRecording = true;
        dictation.isTranscribing = false;
        updateUserMessageEditDictationButton(session, 'recording');
        updateUserMessageEditActionState(session);
    } catch (error) {
        handleError(error);
    }
}

async function transcribeUserMessageEditAudio(session, audioBlob, durationSeconds = null) {
    const dictation = session.dictation;
    if (!dictation || dictation.isTranscribing) {
        return;
    }
    dictation.isTranscribing = true;
    dictation.abortController = new AbortController();
    updateUserMessageEditDictationButton(session, 'transcribing');

    try {
        let extension = 'webm';
        const mimeType = String(audioBlob.type || '');
        if (mimeType.includes('mp4') || mimeType.includes('m4a')) extension = 'mp4';
        if (mimeType.includes('wav')) extension = 'wav';
        if (mimeType.includes('mpeg') || mimeType.includes('mp3')) extension = 'mp3';

        const formData = new FormData();
        formData.append('audio', audioBlob, `recording.${extension}`);
        if (Number.isFinite(Number(durationSeconds)) && Number(durationSeconds) > 0) {
            formData.append('duration_seconds', String(Number(durationSeconds)));
        }

        const response = await window.authedFetch('/api/v1/llm/transcribe', {
            method: 'POST',
            body: formData,
            signal: dictation.abortController.signal,
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            const errorMessage = typeof omlorixFormatTranscriptionErrorMessage === 'function'
                ? omlorixFormatTranscriptionErrorMessage(
                    errorData,
                    getStreamText('chat_dictation_transcription_failed', 'Transcription failed. Please try again.'),
                    response.status,
                )
                : getStreamTextFormatted('chat_dictation_transcription_failed_http_status', 'Transcription failed. Please try again. (HTTP {status})', {
                    status: Number(response.status),
                });
            const requestError = new Error(errorMessage);
            const limitClassification = (
                typeof omlorixClassifyTranscriptionLimit === 'function'
                    ? omlorixClassifyTranscriptionLimit(errorData)
                    : {
                        isDictationInProgress: (
                            errorData?.detail?.code === 'user_dictation_in_progress'
                            || (
                                errorData?.detail?.code === 'user_dictation_rate_limited'
                                && errorData?.detail?.reason === 'active_reservation'
                            )
                        ),
                        isDictationRateLimit: (
                            errorData?.detail?.code === 'user_dictation_rate_limited'
                            && errorData?.detail?.reason !== 'active_reservation'
                        ),
                    }
            );
            requestError.isDictationInProgress = limitClassification.isDictationInProgress;
            requestError.isDictationRateLimit = limitClassification.isDictationRateLimit;
            if (requestError.isDictationInProgress) {
                requestError.message = getStreamText(
                    'chat_live_transcription_in_progress',
                    'Another live transcription is already active. Stop it or wait a moment, then try again.',
                );
            } else if (requestError.isDictationRateLimit) {
                requestError.message = getStreamText(
                    'us_rate_limits_feature_exceeded_notice',
                    'Minute limit reached. Try again after the reset time.',
                );
            }
            throw requestError;
        }
        const data = await response.json();
        const transcribedText = String(data?.text || '').trim();
        if (!transcribedText) {
            notifyWarning?.(getStreamText('chat_dictation_no_speech_detected', 'No speech detected in the recording.'));
            return;
        }
        insertUserMessageEditTextAtCursor(session, transcribedText);
        notifySuccess?.(getStreamText('chat_dictation_transcription_complete', 'Transcription complete.'));
    } catch (error) {
        if (error?.name !== 'AbortError') {
            console.error('Edit message transcription failed', error);
            if (error?.isDictationRateLimit) {
                notifyWarning?.(error.message);
            } else if (error?.isDictationInProgress) {
                notifyWarning?.(error.message);
            } else {
                notifyError?.(error.message || getStreamText('chat_dictation_transcription_failed', 'Transcription failed. Please try again.'));
            }
        }
    } finally {
        dictation.isTranscribing = false;
        dictation.abortController = null;
        updateUserMessageEditDictationButton(session, 'idle');
        updateUserMessageEditActionState(session);
    }
}

async function toggleUserMessageEditDictation(session) {
    if (!session?.dictationSupported || !session?.dictation) {
        return;
    }
    const dictation = session.dictation;
    if (dictation.isTranscribing) {
        if (dictation.usesLiveTranscription) {
            window.LiveTranscription?.cancel?.();
            if (dictation.liveBase) {
                applyUserMessageEditLiveTranscript(session, '');
            }
            dictation.liveBase = null;
            dictation.usesLiveTranscription = false;
            session.textarea.readOnly = false;
        }
        if (dictation.abortController) {
            dictation.abortController.abort();
        }
        dictation.isTranscribing = false;
        updateUserMessageEditDictationButton(session, 'idle');
        updateUserMessageEditActionState(session);
        return;
    }

    if (dictation.isRecording) {
        dictation.isRecording = false;
        updateUserMessageEditDictationButton(session, 'transcribing');
        if (dictation.usesLiveTranscription) {
            dictation.isTranscribing = true;
            window.LiveTranscription.stop().catch(() => {
                // The controller forwards failures through onError.
            });
            updateUserMessageEditActionState(session);
            return;
        }
        if (dictation.mediaRecorder && dictation.mediaRecorder.state !== 'inactive') {
            dictation.mediaRecorder.stop();
        }
        return;
    }

    const skipLiveOnce = Boolean(dictation.skipLiveOnce);
    dictation.skipLiveOnce = false;
    const canUseLive = Boolean(
        !skipLiveOnce
        && window.LiveTranscription?.isReady?.()
        && window.LiveTranscription?.isSupported?.()
    );
    if (canUseLive) {
        await startUserMessageEditLiveDictation(session);
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        dictation.usesLiveTranscription = false;
        dictation.stream = stream;
        dictation.audioChunks = [];
        dictation.recordingStartedAt = performance.now();
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
            ? 'audio/webm;codecs=opus'
            : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '');
        const mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
        dictation.mediaRecorder = mediaRecorder;

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                dictation.audioChunks.push(event.data);
            }
        };
        mediaRecorder.onstop = async () => {
            const durationSeconds = dictation.recordingStartedAt == null
                ? null
                : Math.max((performance.now() - dictation.recordingStartedAt) / 1000, 0.001);
            dictation.recordingStartedAt = null;
            if (dictation.stream) {
                dictation.stream.getTracks().forEach((track) => track.stop());
                dictation.stream = null;
            }
            if (!dictation.audioChunks.length) {
                updateUserMessageEditDictationButton(session, 'idle');
                updateUserMessageEditActionState(session);
                return;
            }
            await transcribeUserMessageEditAudio(
                session,
                new Blob(dictation.audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' }),
                durationSeconds,
            );
        };
        mediaRecorder.onerror = () => {
            stopUserMessageEditDictationResources(session);
            dictation.isRecording = false;
            dictation.isTranscribing = false;
            updateUserMessageEditDictationButton(session, 'idle');
            notifyError?.(getStreamText('chat_dictation_recording_failed', 'Recording failed. Please try again.'));
        };
        mediaRecorder.start(1000);
        dictation.isRecording = true;
        updateUserMessageEditDictationButton(session, 'recording');
        updateUserMessageEditActionState(session);
    } catch (error) {
        console.error('Failed to start dictation for edit composer', error);
        notifyError?.(getStreamText('chat_dictation_could_not_start_recording', 'Could not start recording. Please try again.'));
    }
}

async function handleUserMessageEditFileSelection(session, files) {
    const helpers = getUserMessageEditAttachmentHelpers();
    if (!session || !Array.isArray(files) || !files.length) {
        return;
    }
    if (typeof helpers.uploadChatAttachment !== 'function') {
        notifyError?.(getStreamText('chat_file_uploads_unavailable', 'File uploads are not available right now.'));
        return;
    }

    for (const file of files) {
        const supportedByModel = typeof window.isChatFileSupportedForCurrentModel !== 'function'
            || window.isChatFileSupportedForCurrentModel(file);
        if (!supportedByModel) {
            notifyWarning?.(getStreamTextFormatted('chat_file_unsupported_current_model', '"{name}" is not supported by the current model.', {
                name: file.name,
            }));
            continue;
        }
        const withinLimit = typeof helpers.isWithinChatAttachmentLimit === 'function'
            ? helpers.isWithinChatAttachmentLimit(file)
            : (Number(file?.size || 0) <= USER_MESSAGE_EDIT_UPLOAD_LIMIT_BYTES);
        if (!withinLimit) {
            const sizeLabel = typeof helpers.formatBytes === 'function'
                ? helpers.formatBytes(file.size)
                : `${Math.round(file.size || 0)} B`;
            notifyError?.(getStreamTextFormatted('chat_attachment_file_size_limit_error', 'File{name}{size} exceeds the 100MB limit.', {
                name: ` "${file.name}"`,
                size: ` (${sizeLabel})`,
            }));
            continue;
        }

        const tempId = generateUserMessageEditTempId();
        const tempFile = normalizeUserMessageEditFile({
            id: tempId,
            file_id: tempId,
            original_name: file.name,
            file_type: file.type,
            mime_type: file.type,
            file_size: file.size,
            temporary: true,
            isUploading: true,
            progress: 0,
            file_category: normalizeUserMessageEditCategory({ file_type: file.type, mime_type: file.type }),
        });
        session.currentFiles = [...session.currentFiles, tempFile];
        renderUserMessageEditAttachments(session);

        const abortController = new AbortController();
        session.pendingUploads.set(tempId, { abort: () => abortController.abort() });
        updateUserMessageEditActionState(session);

        try {
            const result = await helpers.uploadChatAttachment(file, {
                signal: abortController.signal,
                onProgress: (progress) => {
                    session.currentFiles = session.currentFiles.map((entry) => (
                        entry.id === tempId ? { ...entry, progress, isUploading: true } : entry
                    ));
                    renderUserMessageEditAttachments(session);
                },
            });
            session.pendingUploads.delete(tempId);

            const uploadedFile = normalizeUserMessageEditFile({
                id: result.fileId,
                file_id: result.fileId,
                original_name: file.name,
                file_type: file.type,
                mime_type: file.type,
                file_size: file.size,
                file_category: normalizeUserMessageEditCategory({ file_category: result.fileCategory, file_type: file.type, mime_type: file.type }),
            });
            session.currentFiles = session.currentFiles.map((entry) => (entry.id === tempId ? uploadedFile : entry));
            renderUserMessageEditAttachments(session);

            if (typeof window.getCachedFilesList === 'function') {
                window.getCachedFilesList({ forceRefresh: true }).catch(() => {});
            }
        } catch (error) {
            session.pendingUploads.delete(tempId);
            session.currentFiles = session.currentFiles.filter((entry) => entry.id !== tempId);
            renderUserMessageEditAttachments(session);
            if (error?.name !== 'AbortError') {
                console.error('Failed to upload edit attachment', error);
                notifyError?.(error.message || getStreamTextFormatted('chat_attachment_upload_failed_named', 'Failed to upload {name}', {
                    name: file.name,
                }));
            }
        } finally {
            updateUserMessageEditActionState(session);
        }
    }
}

const USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_EDGE_PADDING = 12;
const USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_GAP = 8;
const USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_DEFAULT_MAX_HEIGHT = 420;
const USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_MIN_HEIGHT = 96;

function getUserMessageEditUploadDropdownViewportBounds() {
    const visualViewport = typeof window !== 'undefined' ? window.visualViewport : null;
    const viewportTop = visualViewport
        ? Math.max(0, Number(visualViewport.offsetTop) || 0)
        : 0;
    const viewportHeight = visualViewport
        ? Math.max(0, Number(visualViewport.height) || 0)
        : Math.max(0, Number(window.innerHeight) || Number(document.documentElement?.clientHeight) || 0);

    let top = viewportTop + USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_EDGE_PADDING;
    const bottom = viewportTop + viewportHeight - USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_EDGE_PADDING;

    // The chat header is sticky and can visually cover upward-opening menus near
    // the top of the thread. Treat its bottom edge as the usable top boundary.
    document.querySelectorAll('.main-container-header').forEach((header) => {
        const rect = header.getBoundingClientRect();
        const overlapsViewportTop = rect.top <= top && rect.bottom > viewportTop;
        if (overlapsViewportTop && rect.bottom < bottom) {
            top = Math.max(top, rect.bottom + USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_EDGE_PADDING);
        }
    });

    return { top, bottom };
}

function measureUserMessageEditUploadDropdownHeight(dropdown) {
    if (!dropdown) {
        return 0;
    }

    const previousMaxHeight = dropdown.style.maxHeight;
    dropdown.style.maxHeight = '';
    const measuredHeight = dropdown.scrollHeight || dropdown.getBoundingClientRect().height || dropdown.offsetHeight || 0;
    dropdown.style.maxHeight = previousMaxHeight;

    return Number.isFinite(measuredHeight) ? measuredHeight : 0;
}

function resetUserMessageEditUploadDropdownPosition(session) {
    const dropdown = session?.uploadDropdown;
    if (!dropdown) {
        return;
    }

    dropdown.style.maxHeight = '';
    dropdown.style.overflowY = '';
    delete dropdown.dataset.verticalPlacement;
}

function positionUserMessageEditUploadDropdown(session) {
    const dropdown = session?.uploadDropdown;
    const uploadButton = session?.uploadButton;
    if (!dropdown || !uploadButton) {
        return;
    }

    const viewport = getUserMessageEditUploadDropdownViewportBounds();
    const triggerRect = uploadButton.getBoundingClientRect();
    const naturalHeight = measureUserMessageEditUploadDropdownHeight(dropdown);
    const preferredHeight = Math.min(
        naturalHeight || USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_DEFAULT_MAX_HEIGHT,
        USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_DEFAULT_MAX_HEIGHT,
    );
    const spaceAbove = Math.max(0, triggerRect.top - viewport.top - USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_GAP);
    const spaceBelow = Math.max(0, viewport.bottom - triggerRect.bottom - USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_GAP);

    // The edit composer usually sits in the message history instead of the fixed
    // composer area. If the full menu cannot fit upward, prefer opening beneath
    // the paperclip even when the available browser height is tight.
    const shouldOpenDownward = preferredHeight > spaceAbove;
    const availableSpace = Math.max(0, shouldOpenDownward ? spaceBelow : spaceAbove);
    const nextMaxHeight = Math.max(
        Math.min(preferredHeight, availableSpace),
        Math.min(USER_MESSAGE_EDIT_UPLOAD_DROPDOWN_MIN_HEIGHT, availableSpace),
    );

    dropdown.dataset.verticalPlacement = shouldOpenDownward ? 'down' : 'up';
    dropdown.style.maxHeight = nextMaxHeight > 0 ? `${Math.round(nextMaxHeight)}px` : '';
    dropdown.style.overflowY = '';
    window.prepareDropdownOpeningAnimation?.(uploadButton, dropdown, {
        placement: shouldOpenDownward ? 'bottom' : 'top',
    });
}

function bindUserMessageEditUploadDropdownViewportListeners(session) {
    if (!session || session.uploadDropdownViewportHandler || typeof window === 'undefined') {
        return;
    }

    const handler = (event) => {
        const target = event?.target;
        if (typeof Node !== 'undefined' && target instanceof Node && session.uploadDropdown?.contains(target)) {
            return;
        }
        positionUserMessageEditUploadDropdown(session);
    };

    session.uploadDropdownViewportHandler = handler;
    window.addEventListener('resize', handler);
    window.addEventListener('scroll', handler, true);

    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', handler);
        window.visualViewport.addEventListener('scroll', handler);
    }
}

function unbindUserMessageEditUploadDropdownViewportListeners(session) {
    const handler = session?.uploadDropdownViewportHandler;
    if (!handler || typeof window === 'undefined') {
        return;
    }

    window.removeEventListener('resize', handler);
    window.removeEventListener('scroll', handler, true);

    if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', handler);
        window.visualViewport.removeEventListener('scroll', handler);
    }

    session.uploadDropdownViewportHandler = null;
}

function destroyUserMessageEditSession(session) {
    if (!session) {
        return;
    }
    unbindUserMessageEditUploadDropdownViewportListeners(session);
    resetUserMessageEditUploadDropdownPosition(session);
    window.getDropdownPanelNavigator?.(session.uploadDropdown)?.destroy();
    if (session.handleDocumentClick) {
        document.removeEventListener('click', session.handleDocumentClick, true);
    }
    session.closeSaveDropdown?.();
    if (session.handleSaveDropdownDocumentClick) {
        document.removeEventListener('click', session.handleSaveDropdownDocumentClick, true);
    }
    stopUserMessageEditDictationResources(session);
    if (session.dictation?.mediaRecorder && session.dictation.mediaRecorder.state !== 'inactive') {
        try {
            session.dictation.mediaRecorder.stop();
        } catch (_) {}
    }
    session.pendingUploads.forEach((pending) => {
        try {
            pending.abort?.();
        } catch (_) {}
    });
    session.pendingUploads.clear();
    Object.values(session.editQuickpick || {}).forEach((picker) => {
        if (picker?.searchTimer) {
            window.clearTimeout(picker.searchTimer);
        }
    });
    delete session.userMessageContainer.__editSession;
    if (activeUserMessageEditSession === session) {
        activeUserMessageEditSession = null;
    }
}

function createUserMessageEditComposer(session) {
    const editContainer = document.createElement('div');
    editContainer.className = 'user-message-edit-container';
    editContainer.id = `edit-container-${session.messageId}`;

    const attachmentsContainer = document.createElement('div');
    attachmentsContainer.className = 'inline-files user-message-edit-inline-files';
    attachmentsContainer.hidden = true;

    const chatShell = document.createElement('div');
    chatShell.className = 'chat-box user-message-edit-chat-box';

    const top = document.createElement('div');
    top.className = 'chat-box-top';
    const textarea = document.createElement('textarea');
    textarea.className = 'chat-box-top-input user-message-edit-textarea';
    textarea.rows = 1;
    textarea.placeholder = getStreamText('chat_edit_message_placeholder', 'Edit your message...');
    textarea.value = session.originalText;
    top.appendChild(textarea);

    const bottom = document.createElement('div');
    bottom.className = 'chat-box-bottom';

    const left = document.createElement('div');
    left.className = 'chat-box-bottom-div';

    const uploadMenu = document.createElement('div');
    uploadMenu.className = 'chat-box-dropdown user-message-edit-dropdown';

    const uploadButton = document.createElement('button');
    uploadButton.type = 'button';
    uploadButton.className = 'om-button left';
    uploadButton.setAttribute('aria-label', getStreamText('chat_attach_files', 'Attach files'));
    uploadButton.setAttribute('aria-expanded', 'false');
    uploadButton.innerHTML = document.getElementById('chatBoxFilesButton')?.innerHTML
        || Icons.chatFiles;

    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.multiple = true;
    fileInput.style.display = 'none';

    const editAttachmentTarget = {
        handleFileSelection: (files) => handleUserMessageEditFileSelection(session, Array.from(files || [])),
        addUploadedFiles: (files) => {
            const nextFiles = normalizeUserMessageEditFiles([...(session.currentFiles || []), ...(Array.isArray(files) ? files : [])]);
            session.currentFiles = nextFiles;
            renderUserMessageEditAttachments(session);
        },
    };

    const closeUploadDropdown = () => {
        uploadDropdown.classList.remove('open');
        uploadDropdown.setAttribute('aria-hidden', 'true');
        uploadButton.setAttribute('aria-expanded', 'false');
        window.getDropdownPanelNavigator?.(uploadDropdown)?.reset({ focus: false });
        Object.values(session.editQuickpick || {}).forEach((picker) => {
            if (picker?.searchInput) picker.searchInput.value = '';
        });
        unbindUserMessageEditUploadDropdownViewportListeners(session);
        resetUserMessageEditUploadDropdownPosition(session);
    };

    const uploadDropdown = typeof window.ChatFilesMenu?.createMenuElement === 'function'
        ? window.ChatFilesMenu.createMenuElement({
            onPanelHeightChange: (dropdown) => {
                if (dropdown.classList.contains('open')) {
                    requestAnimationFrame(() => positionUserMessageEditUploadDropdown(session));
                }
            },
            onNavigate: ({ panelName }) => {
                if (panelName === 'files') {
                    void loadUserMessageEditQuickpickFiles(session);
                } else if (panelName === 'chats') {
                    void loadUserMessageEditChatReferences(session);
                }
            },
            actions: {
                meeting: () => {
                    closeUploadDropdown();
                    window.ChatFilesMenu?.actions?.openMeeting?.();
                },
                computer: () => {
                    closeUploadDropdown();
                    fileInput.click();
                },
                screenCapture: () => {
                    closeUploadDropdown();
                    void window.ChatFilesMenu?.actions?.quickScreenCapture?.({ attachmentTarget: editAttachmentTarget });
                },
                googleDrive: () => {
                    closeUploadDropdown();
                    window.ChatFilesMenu?.actions?.openGoogleDrive?.({ attachmentTarget: editAttachmentTarget });
                },
            },
        })
        : (() => {
            const fallbackDropdown = document.createElement('div');
            fallbackDropdown.className = 'select-dropdown';
            fallbackDropdown.setAttribute('role', 'menu');
            fallbackDropdown.setAttribute('aria-hidden', 'true');
            fallbackDropdown.innerHTML = `
                <div class="select-dropdown-item">
                    <button type="button" class="select-dropdown-button" data-edit-upload="computer" role="menuitem">
                        <span>${escapeStreamHtml(getStreamText('chat_files_upload_from_computer', 'Upload files from computer'))}</span>
                    </button>
                </div>
            `;
            fallbackDropdown.querySelector('[data-edit-upload="computer"]')?.addEventListener('click', () => {
                closeUploadDropdown();
                fileInput.click();
            });
            return fallbackDropdown;
        })();

    uploadDropdown.id = `edit-attachments-menu-${session.messageId}`;
    uploadButton.setAttribute('aria-controls', uploadDropdown.id);

    const createQuickpickState = (name) => ({
        searchInput: uploadDropdown.querySelector(`[data-chat-files-search="${name}"]`),
        scrollRegion: uploadDropdown.querySelector(`[data-chat-files-scroll="${name}"]`),
        list: uploadDropdown.querySelector(`[data-chat-files-list="${name}"]`),
        empty: uploadDropdown.querySelector(`[data-chat-files-empty="${name}"]`),
        items: [],
        total: 0,
        hasMore: false,
        loading: false,
        requestId: 0,
        searchTimer: null,
    });
    session.editQuickpick = {
        files: createQuickpickState('files'),
        chats: createQuickpickState('chats'),
    };

    session.editQuickpick.files.searchInput?.addEventListener('input', () => {
        const picker = session.editQuickpick.files;
        if (picker.searchTimer) window.clearTimeout(picker.searchTimer);
        picker.searchTimer = window.setTimeout(() => {
            void loadUserMessageEditQuickpickFiles(session);
        }, USER_MESSAGE_EDIT_FILES_SEARCH_DEBOUNCE_MS);
    });
    session.editQuickpick.files.scrollRegion?.addEventListener('scroll', () => {
        maybeLoadMoreUserMessageEditFiles(session);
    }, { passive: true });
    session.editQuickpick.chats.searchInput?.addEventListener('input', () => {
        const picker = session.editQuickpick.chats;
        if (picker.searchTimer) window.clearTimeout(picker.searchTimer);
        picker.searchTimer = window.setTimeout(() => {
            void loadUserMessageEditChatReferences(session, { forceRefresh: true });
        }, USER_MESSAGE_EDIT_FILES_SEARCH_DEBOUNCE_MS);
    });
    session.editQuickpick.chats.scrollRegion?.addEventListener('scroll', () => {
        maybeLoadMoreUserMessageEditChatReferences(session);
    }, { passive: true });

    uploadButton.setAttribute(
        'aria-haspopup',
        uploadDropdown.getAttribute('role') === 'dialog' ? 'dialog' : 'menu',
    );

    uploadMenu.appendChild(uploadButton);
    uploadMenu.appendChild(uploadDropdown);
    uploadMenu.appendChild(fileInput);
    left.appendChild(uploadMenu);

    const right = document.createElement('div');
    right.className = 'chat-box-bottom-div user-message-edit-toolbar';

    const voiceButton = document.createElement('button');
    voiceButton.type = 'button';
    voiceButton.className = 'om-button';
    voiceButton.setAttribute('aria-label', getStreamText('chat_voice_input', 'Voice input'));
    voiceButton.innerHTML = getUserMessageEditVoiceIcon();

    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.className = 'om-button border';
    cancelButton.textContent = getStreamText('common_cancel', 'Cancel');

    const saveButton = document.createElement('button');
    saveButton.type = 'button';
    saveButton.className = 'user-message-edit-btn-save';
    saveButton.textContent = getStreamText('common_save', 'Save');

    // The alternate action is intentionally created only for the latest
    // user/assistant pair and only when the current group grants regeneration.
    // The normal Save button remains the predictable default action.
    const regenerationTarget = getUserMessageEditRegenerationTarget(session);
    let saveActions = null;
    let saveMenuButton = null;
    let saveDropdown = null;
    let saveAndRegenerateButton = null;
    let closeSaveDropdown = null;

    if (regenerationTarget) {
        saveActions = document.createElement('div');
        saveActions.className = 'user-message-edit-save-actions';
        saveButton.classList.add('user-message-edit-btn-save-split');

        saveMenuButton = document.createElement('button');
        saveMenuButton.type = 'button';
        saveMenuButton.className = 'user-message-edit-save-menu-btn';
        saveMenuButton.setAttribute('aria-label', getStreamText('chat_edit_save_options', 'Open save options'));
        saveMenuButton.setAttribute('title', getStreamText('chat_edit_save_options', 'Open save options'));
        saveMenuButton.setAttribute('aria-haspopup', 'menu');
        saveMenuButton.setAttribute('aria-expanded', 'false');
        saveMenuButton.setAttribute('aria-controls', `edit-save-menu-${session.messageId}`);
        saveMenuButton.innerHTML = Icons.chevron;

        saveDropdown = document.createElement('div');
        saveDropdown.id = `edit-save-menu-${session.messageId}`;
        saveDropdown.className = 'select-dropdown user-message-edit-save-dropdown';
        saveDropdown.setAttribute('role', 'menu');
        saveDropdown.setAttribute('aria-hidden', 'true');

        const saveAndRegenerateItem = document.createElement('div');
        saveAndRegenerateItem.className = 'select-dropdown-item';
        saveAndRegenerateButton = document.createElement('button');
        saveAndRegenerateButton.type = 'button';
        saveAndRegenerateButton.className = 'select-dropdown-button user-message-edit-save-regenerate-btn';
        saveAndRegenerateButton.setAttribute('role', 'menuitem');
        saveAndRegenerateButton.textContent = getStreamText('chat_edit_save_and_regenerate', 'Save & regenerate');
        saveAndRegenerateItem.appendChild(saveAndRegenerateButton);
        saveDropdown.appendChild(saveAndRegenerateItem);

        closeSaveDropdown = ({ restoreFocus = false } = {}) => {
            saveDropdown.classList.remove('open');
            saveDropdown.setAttribute('aria-hidden', 'true');
            // The chevron rotation is handled purely in CSS via the
            // [aria-expanded] state, so no icon swap is needed here.
            saveMenuButton.setAttribute('aria-expanded', 'false');
            if (restoreFocus && !saveMenuButton.disabled) {
                saveMenuButton.focus();
            }
        };

        const openSaveDropdown = () => {
            if (saveMenuButton.disabled) {
                return;
            }
            window.prepareDropdownOpeningAnimation?.(saveMenuButton, saveDropdown);
            saveDropdown.classList.add('open');
            saveDropdown.setAttribute('aria-hidden', 'false');
            // CSS rotates the chevron based on aria-expanded; keep a single icon.
            saveMenuButton.setAttribute('aria-expanded', 'true');
        };

        saveMenuButton.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (saveDropdown.classList.contains('open')) {
                closeSaveDropdown();
            } else {
                openSaveDropdown();
            }
        });
        saveMenuButton.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openSaveDropdown();
                saveAndRegenerateButton.focus();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                closeSaveDropdown();
            }
        });
        saveAndRegenerateButton.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeSaveDropdown({ restoreFocus: true });
            }
        });
        saveAndRegenerateButton.addEventListener('click', async (event) => {
            event.preventDefault();
            event.stopPropagation();
            closeSaveDropdown();
            await saveUserMessageEdit(session, { regenerateAfterSave: true });
        });

        const handleSaveDropdownDocumentClick = (event) => {
            if (!saveActions.contains(event.target)) {
                closeSaveDropdown();
            }
        };
        session.handleSaveDropdownDocumentClick = handleSaveDropdownDocumentClick;
        document.addEventListener('click', handleSaveDropdownDocumentClick, true);

        saveActions.appendChild(saveButton);
        saveActions.appendChild(saveMenuButton);
        saveActions.appendChild(saveDropdown);
    }

    right.appendChild(voiceButton);
    right.appendChild(cancelButton);
    right.appendChild(saveActions || saveButton);

    bottom.appendChild(left);
    bottom.appendChild(right);
    chatShell.appendChild(top);
    chatShell.appendChild(bottom);

    editContainer.appendChild(attachmentsContainer);
    editContainer.appendChild(chatShell);

    session.editContainer = editContainer;
    session.attachmentsContainer = attachmentsContainer;
    session.textarea = textarea;
    session.uploadButton = uploadButton;
    session.uploadDropdown = uploadDropdown;
    session.fileInput = fileInput;
    session.voiceButton = voiceButton;
    session.cancelButton = cancelButton;
    session.saveButton = saveButton;
    session.saveMenuButton = saveMenuButton;
    session.saveDropdown = saveDropdown;
    session.saveAndRegenerateButton = saveAndRegenerateButton;
    session.closeSaveDropdown = closeSaveDropdown;
    const fileTranscriptionReady = (() => {
        if (typeof window.chatSetup?.file_transcription_ready === 'boolean') {
            return window.chatSetup.file_transcription_ready;
        }
        try {
            const stored = localStorage.getItem('file_transcription_ready');
            return stored === null ? true : stored === 'true';
        } catch (_) {
            return true;
        }
    })();
    session.dictationSupported = Boolean(
        (
            fileTranscriptionReady
            && navigator.mediaDevices
            && navigator.mediaDevices.getUserMedia
            && window.MediaRecorder
        )
        || (
            window.LiveTranscription?.isReady?.()
            && window.LiveTranscription?.isSupported?.()
        )
    );
    session.dictation = {
        isRecording: false,
        isTranscribing: false,
        mediaRecorder: null,
        audioChunks: [],
        stream: null,
        abortController: null,
        recordingStartedAt: null,
        usesLiveTranscription: false,
        liveBase: null,
        fileTranscriptionReady,
        skipLiveOnce: false,
    };

    textarea.addEventListener('input', () => {
        autoResizeUserMessageEditTextarea(textarea);
        updateUserMessageEditActionState(session);
    });
    textarea.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            exitUserMessageEditMode(session.messageId, session.userMessageContainer);
        }
        if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            saveUserMessageEdit(session);
        }
    });

    uploadButton.addEventListener('click', (event) => {
        event.preventDefault();
        const shouldOpen = !uploadDropdown.classList.contains('open');
        if (!shouldOpen) {
            closeUploadDropdown();
            return;
        }
        window.getDropdownPanelNavigator?.(uploadDropdown)?.reset({ focus: false });
        window.ChatFilesMenu?.updateMenuVisibility?.(uploadDropdown);
        positionUserMessageEditUploadDropdown(session);
        uploadDropdown.classList.add('open');
        uploadDropdown.setAttribute('aria-hidden', 'false');
        uploadButton.setAttribute('aria-expanded', 'true');
        void window.ChatFilesMenu?.refreshConnectionsAvailability?.();
        bindUserMessageEditUploadDropdownViewportListeners(session);
    });
    const handleDocumentClick = (event) => {
        if (session.destroyed || !uploadMenu.contains(event.target)) {
            closeUploadDropdown();
        }
    };
    session.handleDocumentClick = handleDocumentClick;
    document.addEventListener('click', handleDocumentClick, true);

    fileInput.addEventListener('change', async (event) => {
        await handleUserMessageEditFileSelection(session, Array.from(event.target.files || []));
        event.target.value = '';
    });
    voiceButton.addEventListener('click', (event) => {
        event.preventDefault();
        toggleUserMessageEditDictation(session);
    });
    cancelButton.addEventListener('click', () => {
        exitUserMessageEditMode(session.messageId, session.userMessageContainer);
    });
    saveButton.addEventListener('click', () => {
        saveUserMessageEdit(session);
    });

    if (!session.dictationSupported) {
        voiceButton.style.display = 'none';
    } else {
        updateUserMessageEditDictationButton(session, 'idle');
    }
    autoResizeUserMessageEditTextarea(textarea);
    renderUserMessageEditAttachments(session);
    updateUserMessageEditActionState(session);

    return editContainer;
}

function enterUserMessageEditMode(messageId, userMessageContainer, userMessage, userMessageContent) {
    if (!userMessageContainer || !userMessage || !userMessageContent) {
        return;
    }
    if (userMessageContainer.classList.contains('editing')) {
        return;
    }
    if (activeUserMessageEditSession && activeUserMessageEditSession.userMessageContainer !== userMessageContainer) {
        exitUserMessageEditMode(activeUserMessageEditSession.messageId, activeUserMessageEditSession.userMessageContainer);
    }

    const storedState = userMessageContainer.__editState || {};
    const originalText = String(storedState.text ?? userMessageContent.getAttribute('data-raw-content') ?? userMessageContent.innerText ?? '').trim();
    const originalFiles = normalizeUserMessageEditFiles(storedState.files || []);
    const originalChatReferences = normalizeUserMessageEditChatReferences(storedState.chatReferences || []);

    const session = {
        messageId,
        userMessageContainer,
        userMessage,
        userMessageContent,
        columnWrapper: userMessageContainer.parentElement,
        originalText,
        originalFiles,
        originalChatReferences,
        originalFilesSignature: getUserMessageEditFileSignature(originalFiles),
        originalChatReferencesSignature: getUserMessageEditChatReferenceSignature(originalChatReferences),
        currentFiles: [...originalFiles],
        currentChatReferences: [...originalChatReferences],
        pendingUploads: new Map(),
        isSaving: false,
        destroyed: false,
    };

    userMessageContainer.classList.add('editing');
    userMessageContainer.__editSession = session;
    activeUserMessageEditSession = session;
    setUserMessageEditExternalFilesHidden(session, true);

    const editContainer = createUserMessageEditComposer(session);
    userMessage.appendChild(editContainer);
    requestAnimationFrame(() => {
        session.textarea?.focus();
        const valueLength = session.textarea?.value?.length || 0;
        session.textarea?.setSelectionRange(valueLength, valueLength);
    });
}

function exitUserMessageEditMode(messageId, userMessageContainer) {
    const session = userMessageContainer?.__editSession;
    userMessageContainer?.classList.remove('editing');
    if (session?.editContainer?.parentElement) {
        session.editContainer.remove();
    } else {
        const editContainer = document.getElementById(`edit-container-${messageId}`);
        editContainer?.remove();
    }
    setUserMessageEditExternalFilesHidden(session, false);
    if (session) {
        session.destroyed = true;
        destroyUserMessageEditSession(session);
    }
}

async function saveUserMessageEdit(session, { regenerateAfterSave = false } = {}) {
    if (!session?.textarea) {
        return;
    }
    const newContent = session.textarea.value.trim();
    if (!newContent) {
        notifyError?.(getStreamText('chat_message_empty_error', 'Message cannot be empty'));
        return;
    }
    if (!userMessageEditHasChanges(session)) {
        exitUserMessageEditMode(session.messageId, session.userMessageContainer);
        return;
    }

    // Capture and validate the response before saving because a successful save
    // exits edit mode and destroys the transient session UI.
    const regenerationTarget = regenerateAfterSave
        ? getUserMessageEditRegenerationTarget(session)
        : null;
    if (regenerateAfterSave && !regenerationTarget) {
        notifyWarning?.(getStreamText('chat_regenerate_latest_only', 'Only the latest response can be regenerated'));
        return false;
    }

    session.isSaving = true;
    updateUserMessageEditActionState(session);

    try {
        const payload = buildUserMessageEditPayload(session, newContent);
        if (!payload.message_id) {
            throw new Error(getStreamText('chat_message_still_sending_error', 'Message is still being sent. Try again in a moment.'));
        }
        const response = await window.authedFetch('/api/v1/chats/messages/edit', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || getStreamText('chat_message_save_failed', 'Failed to save message'));
        }

        renderUserMessageTextContent(session.userMessageContent, newContent);
        rerenderUserMessageFiles(session.messageId, session.currentFiles, session.columnWrapper);
        session.userMessageContainer.__editState = {
            messageId: session.messageId,
            text: newContent,
            files: normalizeUserMessageEditFiles(session.currentFiles),
            chatReferences: normalizeUserMessageEditChatReferences(session.currentChatReferences),
        };

        exitUserMessageEditMode(session.messageId, session.userMessageContainer);
        notifySuccess?.(getStreamText('chat_message_updated', 'Message updated'));

        // Saving is intentionally committed first. A failed regeneration must
        // never roll back or misrepresent the user's successful message edit.
        if (regenerationTarget && typeof window.triggerRegeneration === 'function') {
            try {
                await window.triggerRegeneration(regenerationTarget.assistantMessageId);
            } catch (regenerationError) {
                // triggerRegeneration normally handles and reports its own
                // errors. This boundary protects the already-committed edit if
                // an unexpected caller-level exception escapes that function.
                console.error('Message saved, but regeneration failed:', regenerationError);
                notifyError?.(getStreamText('chat_regenerate_failed', 'Failed to regenerate response'));
            }
        }
        return true;
    } catch (error) {
        console.error('Failed to save message edit:', error);
        session.isSaving = false;
        updateUserMessageEditActionState(session);
        notifyError?.(error.message || getStreamText('chat_message_save_failed', 'Failed to save message'));
        return false;
    }
}
