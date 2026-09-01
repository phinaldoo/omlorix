function appendUserContent(messageId, message, files, chatReferences = []) {
    // Check if a user message with the message id already exists
    const checkIfExists = document.getElementById('u-' + messageId);
    if (checkIfExists) return;
    const chatAreaContainer = document.getElementById('chatAreaContainer');
    ensureTranscriptAccessibility(chatAreaContainer);

    clearAssistantRegenerateButtons();
    const existingSpacer = chatAreaContainer.querySelector('.dynamic-scroll-spacer');

    // Wrapper area (matches index structure)
    const userMessageArea = document.createElement('div');
    userMessageArea.className = 'user-message-area';

    // Create a column wrapper for files and message to stack vertically
    const columnWrapper = document.createElement('div');
    columnWrapper.style.display = 'flex';
    columnWrapper.style.flexDirection = 'column';
    columnWrapper.style.gap = '10px';
    columnWrapper.style.alignItems = 'flex-end';
    columnWrapper.style.width = '100%';
    columnWrapper.style.maxWidth = '100%';
    userMessageArea.appendChild(columnWrapper);

    const hasUserText = Boolean(String(message ?? '').trim().length);
    let userMessageContainer = null;

    if (hasUserText) {
        // Message container
        userMessageContainer = document.createElement('div');
        userMessageContainer.className = 'user-message-container';
        userMessageContainer.dataset.userMessageId = messageId;
        userMessageContainer.dataset.bookmarked = 'false';
        columnWrapper.appendChild(userMessageContainer);

        // Message bubble
        const userMessage = document.createElement('div');
        userMessage.className = 'user-message';
        userMessageContainer.appendChild(userMessage);

        // Keep the prompt content inside a measured wrapper so long user prompts
        // can collapse without changing copy/edit behavior or assistant messages.
        const userMessageExpandableContent = document.createElement('div');
        userMessageExpandableContent.className = 'user-message-expandable-content';
        userMessage.appendChild(userMessageExpandableContent);

        // Markdown content holder
        const userMessageContent = document.createElement('div');
        userMessageContent.id = 'u-' + messageId;
        userMessageContent.className = 'user-message-content';
        userMessageExpandableContent.appendChild(userMessageContent);
        userMessageContent.setAttribute('data-raw-content', message);
        
        // Conditionally render markdown for user message based on settings
        const renderUserMarkdown = safeGetLocalStorageItem('render_user_messages_markdown');
        const userMessageText = String(message ?? '');
        if (renderUserMarkdown === 'true') {
            renderMarkdownContent(userMessageContent, userMessageText);
        } else {
            userMessageContent.innerHTML = '';
            userMessageContent.textContent = userMessageText;
            userMessageContent.classList.remove('markdown-body');
        }

        userMessage.appendChild(createUserMessageExpandControl(messageId, userMessageContainer));
        scheduleUserMessageExpandableRefresh(userMessageContainer);

        userMessageContainer.__editState = {
            messageId,
            text: userMessageText,
            files: Array.isArray(files) ? files : [],
            chatReferences: Array.isArray(chatReferences) ? chatReferences : [],
        };

        // Button list (copy/edit/bookmark) as in HTML.
        if (!isChatViewReadOnly()) {
            const buttonList = document.createElement('div');
            buttonList.className = 'user-message-list';

            const showUserEditButton = getChatBooleanSetting('user_message_button_list_edit', true);
            const showUserDeleteButton = getChatBooleanSetting('user_message_button_list_delete', true);

            if (showUserEditButton) {
                const editButton = document.createElement('button');
                editButton.className = 'user-message-list-button';
                editButton.type = 'button';
                editButton.setAttribute('aria-label', getChatA11yText('chat_sr_edit_user_message', 'Edit message'));
                editButton.title = getChatA11yText('chat_sr_edit_user_message', 'Edit message');
                if (typeof Icons.edit !== 'undefined') editButton.innerHTML = Icons.edit;
                editButton.addEventListener('click', () => {
                    enterUserMessageEditMode(messageId, userMessageContainer, userMessage, userMessageContent);
                });
                buttonList.appendChild(editButton);
            }

            const copyButton = document.createElement('button');
            copyButton.className = 'user-message-list-button';
            copyButton.type = 'button';
            copyButton.setAttribute('aria-label', getChatA11yText('chat_sr_copy_user_message', 'Copy message'));
            copyButton.title = getChatA11yText('chat_sr_copy_user_message', 'Copy message');
            if (typeof Icons.copy !== 'undefined') copyButton.innerHTML = Icons.copy;
            copyButton.addEventListener('click', async () => {
                const textToCopy = userMessageContent.getAttribute('data-raw-content') || userMessageContent.innerText || '';
                try {
                    await writeTextToClipboardWithFallback(textToCopy);
                    reportChatCopyFeedback({
                        success: true,
                        key: 'chat_copy_message_success',
                        fallback: 'Message copied to clipboard.',
                    });
                    if (typeof Icons.check !== 'undefined') {
                        const original = copyButton.innerHTML;
                        copyButton.innerHTML = Icons.check;
                        copyButton.disabled = true;
                        setTimeout(() => {
                            copyButton.innerHTML = original;
                            copyButton.disabled = false;
                        }, 3000);
                    }
                } catch (err) {
                    console.error('User message copy failed:', err);
                    reportChatCopyFeedback({
                        success: false,
                        key: 'chat_copy_message_error',
                        fallback: 'Failed to copy message.',
                    });
                    copyButton.disabled = false;
                }
            });

            buttonList.appendChild(copyButton);

            // Add user message more menu with bookmark and delete actions
            const userMoreMenu = createUserMessageMoreMenu(messageId, userMessageContainer, {
                showDelete: showUserDeleteButton,
            });
            buttonList.appendChild(userMoreMenu);

            if (buttonList.childElementCount > 0) {
                applyMessageActionToolbarAccessibility(
                    buttonList,
                    getChatA11yText('chat_sr_user_actions_toolbar', 'Message actions'),
                    userMessageContainer
                );
                userMessageContainer.appendChild(buttonList);
            }
        }
    } else {
        // Create a hidden anchor element so duplicate checks still work even without text content
        const hiddenAnchor = document.createElement('div');
        hiddenAnchor.id = 'u-' + messageId;
        hiddenAnchor.style.display = 'none';
        hiddenAnchor.setAttribute('data-raw-content', '');
        columnWrapper.appendChild(hiddenAnchor);
    }

    /*
     * Chat references are request context, not user-visible attachments.
     * Keep them in __editState above so message editing can preserve or change
     * the references, but never add reference cards to the chat transcript.
     */
    appendUserFiles(messageId, files, columnWrapper);
    
    // Keep the spacer mounted at the end so sending the next message does not clamp scrollTop upward.
    if (existingSpacer && existingSpacer.parentElement === chatAreaContainer) {
        chatAreaContainer.insertBefore(userMessageArea, existingSpacer);
    } else {
        chatAreaContainer.appendChild(userMessageArea);
    }
    applyUserMessageAccessibility(userMessageContainer || userMessageArea, { messageId });
}

function appendUserFiles(messageId, files, columnWrapper){
    // Append the User files (audio, image, video, document) in the user message
    // Files are inserted at the beginning of the column wrapper so they appear above the message
    
    if (!files || !Array.isArray(files) || files.length === 0) {
        return;
    }
    
    if (!columnWrapper) {
        return;
    }
    
    // Create the inline files container
    const inlineFilesContainer = document.createElement('div');
    inlineFilesContainer.className = 'inline-files';

    const refreshInlineFilesContainerState = () => {
        const hasGridTiles = inlineFilesContainer.querySelector('.inline-files-element') !== null;
        const hasAudioTiles = inlineFilesContainer.querySelector('.user-inline-audio') !== null;
        inlineFilesContainer.classList.toggle('active', hasGridTiles);
        inlineFilesContainer.classList.toggle(
            'chat-file-cards',
            inlineFilesContainer.querySelector('.chat-file-card') !== null,
        );
        inlineFilesContainer.classList.toggle('inline-files-audio-only', hasAudioTiles && !hasGridTiles);
        if (!hasGridTiles && !hasAudioTiles) {
            inlineFilesContainer.style.display = 'none';
        } else {
            inlineFilesContainer.style.display = '';
        }
    };
    
    // Helper function to get file icon based on mime type
    const getFileIcon = (mimeType) => {
        const iconMap = {
            'application/pdf': 'pdf.svg',
            'image/png': 'png.svg',
            'image/jpeg': 'jpg.svg',
            'image/jpg': 'jpg.svg',
            'image/gif': 'gif.svg',
            'image/bmp': 'bmp.svg',
            'image/svg+xml': 'svg.svg',
            'audio/mpeg': 'mp3.svg',
            'audio/mp3': 'mp3.svg',
            'audio/wav': 'mp3.svg',
            'audio/aac': 'aac.svg',
            'video/mp4': 'mpg.svg',
            'video/avi': 'avi.svg',
            'video/mov': 'mov.svg',
            'video/wmv': 'wmv.svg',
            'video/flv': 'flv.svg',
            'text/plain': 'txt.svg',
            'text/html': 'html.svg',
            'text/css': 'css.svg',
            'application/javascript': 'js.svg',
            'text/javascript': 'js.svg',
            'application/json': 'js.svg',
            'application/xml': 'xml.svg',
            'text/xml': 'xml.svg',
            'application/vnd.ms-excel': 'xls.svg',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xls.svg',
            'application/vnd.ms-powerpoint': 'ppt.svg',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'ppt.svg',
            'application/msword': 'txt.svg',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'txt.svg',
            'application/x-sql': 'sql.svg',
        };
        return iconMap[mimeType] || 'txt.svg';
    };
    
    // Helper function to get file extension label
    const getFileExtension = (filename, mimeType) => {
        if (filename && filename.includes('.')) {
            return filename.split('.').pop().toUpperCase();
        }
        if (mimeType) {
            const parts = mimeType.split('/');
            if (parts.length > 1) {
                return parts[1].toUpperCase();
            }
        }
        return 'FILE';
    };
    
    // Helper function to format file size
    const formatFileSize = (bytes) => {
        if (!bytes || bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        const k = 1024;
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        // Match the composer and edit views without showing a redundant
        // trailing zero (for example, display 1.4 KB instead of 1.40 KB).
        const size = i === 0
            ? String(bytes)
            : (bytes / Math.pow(k, i)).toFixed(1);
        return `${size} ${units[i]}`;
    };
    
    const createInlineFileTile = (file, fileIdOverride = null) => {
        const fileElement = document.createElement('div');
        fileElement.className = 'inline-files-element';
        
        // Icon wrapper
        const iconWrapper = document.createElement('div');
        iconWrapper.className = 'inline-files-element-icon';
        const iconImg = document.createElement('img');
        const mimeType = file.mime_type || file.file_type || '';
        const fileId = fileIdOverride || file.file_id || file.id || null;
        if (fileId) {
            fileElement.dataset.fileId = String(fileId);
        }
        iconImg.src = `/assets/file_svgs/${getFileIcon(mimeType)}`;
        iconImg.alt = getFileExtension(file.original_name, mimeType);
        iconImg.width = 28;
        iconImg.height = 28;
        iconImg.style.display = 'block';
        iconImg.style.objectFit = 'contain';
        iconImg.onerror = function() {
            console.warn(`Failed to load icon: ${this.src}`);
            // Fallback: show extension text if image fails
            this.style.display = 'none';
            const fallbackText = document.createElement('span');
            fallbackText.textContent = getFileExtension(file.original_name, mimeType).substring(0, 3);
            fallbackText.style.fontSize = '12px';
            fallbackText.style.fontWeight = 'bold';
            fallbackText.style.color = 'white';
            iconWrapper.appendChild(fallbackText);
        };
        iconWrapper.appendChild(iconImg);
        
        // Content wrapper
        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'inline-files-element-content';
        
        // Top row (filename)
        const topRow = document.createElement('div');
        topRow.className = 'inline-files-element-content-top';
        const nameEl = document.createElement('p');
        nameEl.textContent = file.original_name || 'File';
        topRow.appendChild(nameEl);
        
        // Bottom row (extension and size)
        const bottomRow = document.createElement('div');
        bottomRow.className = 'inline-files-element-content-bottom';
        const extensionEl = document.createElement('p');
        extensionEl.textContent = getFileExtension(file.original_name, mimeType);
        bottomRow.appendChild(extensionEl);
        
        if (file.file_size) {
            const sizeEl = document.createElement('p');
            sizeEl.textContent = formatFileSize(file.file_size);
            bottomRow.appendChild(sizeEl);
        }
        
        contentWrapper.appendChild(topRow);
        contentWrapper.appendChild(bottomRow);
        
        fileElement.appendChild(iconWrapper);
        fileElement.appendChild(contentWrapper);

        // The file-card enhancer returns a dedicated open/preview button so
        // the sibling download button remains a separate accessible action.
        const previewTarget = enhanceChatTranscriptFileCard(fileElement, file);
        attachPreviewToInlineFile(previewTarget, file);
        return fileElement;
    };

    const appendResolvedUserFile = (entry, { fileId = null, replaceTarget = null } = {}) => {
        const resolved = entry || {};
        const resolvedMeta = resolved.meta || {};
        const resolvedId = fileId || resolved.file_id || resolved.id || null;
        const resolvedType = String(resolved.file_type || resolved.mime_type || resolvedMeta.file_type || resolvedMeta.mime_type || '').toLowerCase();
        const originalName = String(
            resolved.original_name
            || resolved.original_filename
            || resolved.file_name
            || resolvedMeta.original_filename
            || resolvedMeta.original_name
            || 'Audio'
        );
        const resolvedSize = Number(resolved.file_size || resolvedMeta.file_size || 0) || 0;

        let renderedElement = null;
        if (resolvedId && isDisplayableAudioType(resolvedType) && typeof createAssistantInlineAudio === 'function') {
            renderedElement = createAssistantInlineAudio(
                resolvedId,
                {
                    file_id: resolvedId,
                    id: resolvedId,
                    file_type: resolvedType || 'audio/wav',
                    mime_type: resolvedType || 'audio/wav',
                    file_size: resolvedSize,
                    original_name: originalName,
                    original_filename: originalName,
                    meta: {
                        ...(resolvedMeta || {}),
                        original_filename: originalName,
                        mime_type: resolvedType || 'audio/wav',
                    },
                },
                null,
                { source: 'user' }
            );
            renderedElement.classList.add('user-inline-audio');
        } else {
            renderedElement = createInlineFileTile(
                {
                    ...resolved,
                    original_name: originalName,
                    file_type: resolvedType,
                    mime_type: resolvedType,
                    file_size: resolvedSize,
                    file_id: resolvedId,
                    id: resolvedId,
                },
                resolvedId
            );
        }

        if (replaceTarget && replaceTarget.parentElement) {
            replaceTarget.parentElement.replaceChild(renderedElement, replaceTarget);
        } else {
            inlineFilesContainer.appendChild(renderedElement);
        }

        refreshInlineFilesContainerState();
        refreshUnsupportedFileWarningsFromState();
    };

    // Create file elements
    files.forEach(file => {
        if (typeof file === 'string') {
            const fileId = String(file || '').trim();
            if (!fileId) {
                return;
            }
            if (isChatViewReadOnly()) {
                appendResolvedUserFile({ file_id: fileId, original_name: fileId }, { fileId });
                return;
            }
            const placeholder = document.createElement('div');
            placeholder.className = 'inline-files-element';
            placeholder.textContent = getStreamText('chat_attachment_loading_file', 'Loading file...');
            inlineFilesContainer.appendChild(placeholder);
            refreshInlineFilesContainerState();
            fetchChatFileMeta(fileId)
                .then((response) => (response.ok ? response.json() : null))
                .then((resolvedData) => {
                    appendResolvedUserFile(resolvedData || {}, { fileId, replaceTarget: placeholder });
                })
                .catch(() => {
                    appendResolvedUserFile({ file_id: fileId, original_name: fileId }, { fileId, replaceTarget: placeholder });
                });
            return;
        }

        appendResolvedUserFile(file, { fileId: file?.file_id || file?.id || null });
    });

    const realFileCount = Array.isArray(files) ? files.length : inlineFilesContainer.children.length;

    // Two option 9 cards fit comfortably on a wide transcript. Insert one
    // invisible grid cell for an odd count so the first card remains aligned
    // with the right edge of the user-message column.
    if (realFileCount % 2 === 1) {
        const placeholder = document.createElement('div');
        placeholder.className = 'inline-files-element inline-files-align-placeholder';
        inlineFilesContainer.insertBefore(placeholder, inlineFilesContainer.firstChild);
        refreshInlineFilesContainerState();
    }

    // Insert the files container at the beginning of the column wrapper (above the message)
    columnWrapper.insertBefore(inlineFilesContainer, columnWrapper.firstChild);
    refreshInlineFilesContainerState();
}


function normalizeChatFileForPreview(file) {
    if (!file) {
        return null;
    }

    const rawMeta = file.meta || {};
    const fileId = file.file_id || file.id || rawMeta.file_id || rawMeta.id || file.fileId;
    if (!fileId) {
        return null;
    }

    const mimeType = file.mime_type || file.file_type || rawMeta.mime_type || rawMeta.file_type || '';
    const fileSize = typeof file.file_size === 'number'
        ? file.file_size
        : (typeof rawMeta.file_size === 'number' ? rawMeta.file_size : (typeof rawMeta.size === 'number' ? rawMeta.size : 0));
    const originalName = rawMeta.original_filename || file.original_name || file.name || rawMeta.original_name || '';

    const normalized = {
        ...file,
        file_id: fileId,
        id: fileId,
        file_type: mimeType,
        file_size: fileSize,
        meta: {
            ...rawMeta,
            original_filename: originalName,
            mime_type: mimeType,
            file_size: fileSize,
            origin: rawMeta.origin || file.origin || 'user',
        },
    };

    return normalized;
}


