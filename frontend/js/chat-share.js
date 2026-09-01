(function () {
    'use strict';

    const POLL_INTERVAL_MS = 15000;
    const TEXT_PREVIEW_MAX_BYTES = 1024 * 1024;
    const BINARY_PREVIEW_MAX_BYTES = 25 * 1024 * 1024;

    const t = function translate(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const sharedLoadingView = document.getElementById('sharedLoadingView');
    const sharedPasswordView = document.getElementById('sharedPasswordView');
    const sharedErrorView = document.getElementById('sharedErrorView');
    const sharedContent = document.getElementById('sharedChatContent');
    const sharedHeader = document.getElementById('sharedHeader');
    const sharedChatTitle = document.getElementById('sharedChatTitle');
    const sharedErrorMessage = document.getElementById('sharedErrorMessage');
    const sharedRetryBtn = document.getElementById('sharedRetryBtn');

    const passwordForm = document.getElementById('sharedPasswordForm');
    const passwordInput = document.getElementById('sharedPasswordInput');
    const passwordError = document.getElementById('sharedPasswordError');

    const state = {
        shareId: null,
        password: null,
        shareAccessToken: null,
        shareAccessTokenExpiresAt: null,
        userAccessToken: null,
        accessMode: 'public',
        pollTimer: null,
        lastTranscriptSignature: null,
        lastTranscriptPayload: null,
        lastUpdatedAt: null,
    };

    const focusableSelector = [
        'a[href]',
        'area[href]',
        'button:not([disabled])',
        'input:not([disabled]):not([type="hidden"])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        'iframe',
        'audio[controls]',
        'video[controls]',
        '[contenteditable="true"]',
        '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    function isVisibleElement(element) {
        if (!(element instanceof Element)) {
            return false;
        }
        if (element.closest('[hidden], [aria-hidden="true"]')) {
            return false;
        }
        const style = window.getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse';
    }

    function getFocusableElements(container) {
        if (!container || typeof container.querySelectorAll !== 'function') {
            return [];
        }
        return Array.from(container.querySelectorAll(focusableSelector))
            .filter((element) => !element.disabled && isVisibleElement(element));
    }

    function focusFirstElement(container, preferredSelector = null) {
        if (!container) {
            return;
        }
        const preferred = preferredSelector ? container.querySelector(preferredSelector) : null;
        if (preferred && isVisibleElement(preferred) && typeof preferred.focus === 'function') {
            preferred.focus({ preventScroll: true });
            return;
        }
        const focusable = getFocusableElements(container);
        const target = focusable[0] || container;
        if (target && typeof target.focus === 'function') {
            target.focus({ preventScroll: true });
        }
    }

    function trapTabKey(event, container) {
        if (event.key !== 'Tab' || !container) {
            return false;
        }
        const focusable = getFocusableElements(container);
        if (!focusable.length) {
            event.preventDefault();
            if (typeof container.focus === 'function') {
                container.focus({ preventScroll: true });
            }
            return true;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;

        if (!container.contains(active)) {
            event.preventDefault();
            first.focus({ preventScroll: true });
            return true;
        }

        if (event.shiftKey && active === first) {
            event.preventDefault();
            last.focus({ preventScroll: true });
            return true;
        }

        if (!event.shiftKey && active === last) {
            event.preventDefault();
            first.focus({ preventScroll: true });
            return true;
        }

        return false;
    }

    function restoreFocus(target) {
        if (target && typeof target.focus === 'function' && document.contains(target)) {
            target.focus({ preventScroll: true });
        }
    }

    function showView(view) {
        document.body?.setAttribute('data-view', view);
        const views = [sharedLoadingView, sharedPasswordView, sharedErrorView, sharedContent, sharedHeader];
        views.forEach((node) => {
            if (!node) return;
            if (view === 'chat') {
                if (node === sharedContent || node === sharedHeader) {
                    node.hidden = false;
                    return;
                }
                node.hidden = true;
                return;
            }
            if (view === 'password') {
                node.hidden = node !== sharedPasswordView;
                return;
            }
            if (view === 'error') {
                node.hidden = node !== sharedErrorView;
                return;
            }
            node.hidden = node !== sharedLoadingView;
        });
    }

    function setPasswordError(message = '') {
        if (passwordError) {
            passwordError.textContent = message;
        }
        if (passwordInput) {
            passwordInput.setAttribute('aria-invalid', message ? 'true' : 'false');
        }
    }

    function extractShareId() {
        const pathname = String(window.location.pathname || '');
        const parts = pathname.split('/').filter(Boolean);
        const marker = parts.findIndex((part) => part === 'chats');
        if (marker >= 0 && parts[marker + 1] === 'shared' && parts[marker + 2]) {
            return decodeURIComponent(parts[marker + 2]);
        }
        const fromQuery = new URLSearchParams(window.location.search).get('share_id');
        return fromQuery ? decodeURIComponent(fromQuery) : '';
    }

    function initMarkdownRenderer() {
        if (typeof window.markdownit !== 'function') {
            return null;
        }
        const md = window.markdownit({
            html: false,
            linkify: true,
            typographer: true,
            breaks: true,
        });

        const plugins = [
            window.markdownitDeflist,
            window.markdownitAbbr,
            window.markdownitTaskLists,
            window.markdownitMark,
            window.markdownitSup,
            window.markdownitSub,
            window.markdownitAlerts,
        ];

        plugins.forEach((plugin) => {
            if (typeof plugin === 'function') {
                try {
                    md.use(plugin);
                } catch (_) {
                    // ignore plugin init errors
                }
            }
        });

        return md;
    }

    const markdownRenderer = initMarkdownRenderer();

    window.notifyError = window.notifyError || function notifyErrorFallback(message) {
        console.error(message);
    };
    window.notifySuccess = window.notifySuccess || function notifySuccessFallback(message) {
        console.info(message);
    };

    window.authedFetch = window.authedFetch || function authedFetchFallback(url, options) {
        return window.fetch(url, options);
    };

    window.getChatBooleanSetting = function getChatBooleanSettingFallback(_key, fallback) {
        return false;
    };

    if (typeof window.renderMarkdownContent !== 'function') {
        function sanitizeRenderedMarkdown(renderedHtml) {
            if (window.ChatSanitizer && typeof window.ChatSanitizer.sanitizeHtml === 'function') {
                return window.ChatSanitizer.sanitizeHtml(renderedHtml);
            }
            if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
                return window.DOMPurify.sanitize(renderedHtml, {
                    USE_PROFILES: { html: true },
                    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'frame', 'frameset', 'meta', 'base', 'link'],
                    FORBID_ATTR: ['style', 'srcdoc'],
                    ALLOW_DATA_ATTR: false,
                });
            }
            return null;
        }

        window.renderMarkdownContent = function renderMarkdownContentForShare(element, content) {
            if (!element) return;
            const raw = String(content ?? '');
            const normalized = window.ChatMarkdownUtils
                && typeof window.ChatMarkdownUtils.normalizeMarkdownForRender === 'function'
                ? window.ChatMarkdownUtils.normalizeMarkdownForRender(raw)
                : raw;
            if (!markdownRenderer) {
                element.textContent = raw;
                element.classList.remove('markdown-body');
                return;
            }

            const renderedHtml = markdownRenderer.render(normalized);
            const sanitizedHtml = sanitizeRenderedMarkdown(renderedHtml);
            if (sanitizedHtml === null) {
                element.textContent = raw;
                element.classList.remove('markdown-body');
                return;
            }

            element.innerHTML = sanitizedHtml;
            element.classList.add('markdown-body');
            window.ChatMarkdownAlerts?.enhanceIcons?.(element);

            element.querySelectorAll('a[href]').forEach((link) => {
                link.setAttribute('target', '_blank');
                link.setAttribute('rel', 'noopener noreferrer');
            });

            if (window.Prism && typeof window.Prism.highlightAllUnder === 'function') {
                window.Prism.highlightAllUnder(element);
            }
        };
    }

    function sharedFileUrl(fileId, options = {}) {
        const inline = options?.inline === true;
        const params = new URLSearchParams();
        if (inline) {
            params.set('inline', 'true');
        }
        const query = params.toString();
        const base = `/api/v1/chats/shared/files/${encodeURIComponent(String(fileId || '').trim())}`;
        return query ? `${base}?${query}` : base;
    }

    window.getChatFileDownloadUrl = function getChatFileDownloadUrl(fileId, options = {}) {
        return sharedFileUrl(fileId, options);
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function omissionText(reason) {
        const messages = {
            tool_activity_not_published: ['chat_share_omission_tool_activity', 'Tool activity was omitted from this shared transcript.'],
            tool_message_not_published: ['chat_share_omission_tool_message', 'A tool result was omitted from this shared transcript.'],
            interactive_output_not_published: ['chat_share_omission_interactive', 'An interactive result was not approved as a static shared output.'],
            unsupported_block_not_published: ['chat_share_omission_unsupported', 'A result that cannot be shared safely was omitted.'],
        };
        const entry = messages[String(reason || '')] || messages.unsupported_block_not_published;
        return t(entry[0], entry[1]);
    }

    function appendPublishedElement(container, element) {
        if (!container || !element) return false;
        const actions = container.querySelector('.assistant-message-list');
        container.insertBefore(element, actions || null);
        return true;
    }

    function createSharedOmission(block) {
        const notice = document.createElement('div');
        notice.className = 'chat-share-omission';
        notice.setAttribute('role', 'note');
        notice.textContent = omissionText(block?.reason);
        return notice;
    }

    function appendStaticQuiz(card, block) {
        const list = document.createElement('ol');
        list.className = 'chat-share-static-list';
        (Array.isArray(block.items) ? block.items : []).forEach((item) => {
            const listItem = document.createElement('li');
            const prompt = document.createElement('p');
            prompt.className = 'chat-share-static-prompt';
            prompt.textContent = String(item?.prompt || '');
            listItem.appendChild(prompt);
            const options = document.createElement('ul');
            options.className = 'chat-share-static-options';
            (Array.isArray(item?.options) ? item.options : []).forEach((option) => {
                const optionItem = document.createElement('li');
                optionItem.textContent = String(option || '');
                options.appendChild(optionItem);
            });
            listItem.appendChild(options);
            const answer = document.createElement('p');
            answer.className = 'chat-share-static-answer';
            answer.textContent = `${t('chat_share_static_answer', 'Answer')}: ${String(item?.answer || '')}`;
            listItem.appendChild(answer);
            if (item?.explanation) {
                const explanation = document.createElement('p');
                explanation.className = 'chat-share-static-explanation';
                explanation.textContent = String(item.explanation);
                listItem.appendChild(explanation);
            }
            list.appendChild(listItem);
        });
        card.appendChild(list);
    }

    function appendStaticFlashcards(card, block) {
        const list = document.createElement('dl');
        list.className = 'chat-share-static-flashcards';
        (Array.isArray(block.items) ? block.items : []).forEach((item) => {
            const front = document.createElement('dt');
            front.textContent = String(item?.front || '');
            const back = document.createElement('dd');
            back.textContent = String(item?.back || '');
            list.append(front, back);
        });
        card.appendChild(list);
    }

    function createSharedStaticOutput(block) {
        const card = document.createElement('article');
        card.className = `chat-share-static-output chat-share-static-output-${String(block?.output_type || 'unknown')}`;
        card.setAttribute('aria-label', t('chat_share_static_output_aria', 'Reviewed static tool output'));
        const eyebrow = document.createElement('p');
        eyebrow.className = 'chat-share-static-eyebrow';
        eyebrow.textContent = t('chat_share_static_output_label', 'Reviewed static output');
        const title = document.createElement('h3');
        title.textContent = String(block?.title || t('chat_share_static_output_fallback', 'Tool output'));
        card.append(eyebrow, title);
        if (block?.description) {
            const description = document.createElement('p');
            description.className = 'chat-share-static-description';
            description.textContent = String(block.description);
            card.appendChild(description);
        }
        if (block?.output_type === 'quiz') {
            appendStaticQuiz(card, block);
        } else if (block?.output_type === 'flashcards') {
            appendStaticFlashcards(card, block);
        } else if (block?.text) {
            const text = document.createElement('pre');
            text.className = 'chat-share-static-text';
            text.textContent = String(block.text);
            card.appendChild(text);
        }
        return card;
    }

    window.renderSharedChatPublicationBlock = function renderSharedChatPublicationBlock(messageId, block) {
        const container = document.getElementById(`a-${String(messageId || '')}`);
        if (!container) return false;
        const element = block?.type === 'shared_tool_output'
            ? createSharedStaticOutput(block)
            : createSharedOmission(block);
        return appendPublishedElement(container, element);
    };

    window.renderSharedChatTimelineNotice = function renderSharedChatTimelineNotice(message) {
        const container = document.getElementById('chatAreaContainer');
        const block = Array.isArray(message?.content) ? message.content[0] : null;
        if (!container || !block) return false;
        const notice = createSharedOmission(block);
        notice.classList.add('chat-share-timeline-notice');
        container.appendChild(notice);
        return true;
    };

    function normalizeMimeType(type) {
        return String(type || '').trim().toLowerCase().split(';')[0];
    }

    function getFileId(file) {
        return String(file?.file_id || file?.id || '').trim();
    }

    function getFileName(file) {
        const meta = file?.meta && typeof file.meta === 'object' ? file.meta : {};
        return String(
            meta.original_filename
            || file?.original_filename
            || file?.original_name
            || file?.file_name
            || file?.name
            || t('chat_share_file_default_name', 'attachment')
        );
    }

    function getFileExtension(file) {
        const name = getFileName(file);
        return name.includes('.') ? name.split('.').pop().toLowerCase() : '';
    }

    const previewExtensionMimeMap = {
        txt: 'text/plain',
        log: 'text/plain',
        md: 'text/markdown',
        markdown: 'text/markdown',
        json: 'application/json',
        csv: 'text/csv',
        xml: 'application/xml',
        html: 'text/html',
        htm: 'text/html',
        css: 'text/css',
        js: 'application/javascript',
        mjs: 'application/javascript',
        pdf: 'application/pdf',
        png: 'image/png',
        jpg: 'image/jpeg',
        jpeg: 'image/jpeg',
        gif: 'image/gif',
        webp: 'image/webp',
        svg: 'image/svg+xml',
        mp3: 'audio/mpeg',
        wav: 'audio/wav',
        ogg: 'audio/ogg',
        m4a: 'audio/mp4',
        flac: 'audio/flac',
        mp4: 'video/mp4',
        webm: 'video/webm',
        mov: 'video/quicktime',
    };

    function getFileType(file) {
        const meta = file?.meta && typeof file.meta === 'object' ? file.meta : {};
        const direct = normalizeMimeType(file?.file_type || file?.mime_type || meta.file_type || meta.mime_type);
        if (direct && direct !== 'application/octet-stream' && direct !== 'binary/octet-stream') {
            return direct;
        }
        return previewExtensionMimeMap[getFileExtension(file)] || direct || '';
    }

    function getFileSize(file) {
        const candidates = [
            file?.file_size,
            file?.meta?.file_size,
            file?.size,
        ];
        for (const candidate of candidates) {
            const value = Number(candidate);
            if (Number.isFinite(value) && value > 0) {
                return value;
            }
        }
        return 0;
    }

    function isFileOverPreviewLimit(file, limitBytes) {
        const fileSize = getFileSize(file);
        return fileSize > 0 && fileSize > limitBytes;
    }

    function formatPreviewFileSize(bytes) {
        const value = Number(bytes || 0);
        if (!Number.isFinite(value) || value <= 0) {
            return '';
        }
        const units = ['B', 'KB', 'MB', 'GB'];
        const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
        return `${(value / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    }

    function cancelResponseBody(response) {
        try {
            if (response?.body && typeof response.body.cancel === 'function') {
                response.body.cancel();
            }
        } catch (_) {
            // Best effort only.
        }
    }

    function getContentRangeTotal(response) {
        const contentRange = String(response?.headers?.get('Content-Range') || '');
        const match = contentRange.match(/\/(\d+)$/);
        if (!match) return 0;
        const total = Number(match[1]);
        return Number.isFinite(total) && total > 0 ? total : 0;
    }

    function isTextResponseTruncated(response, maxBytes) {
        const total = getContentRangeTotal(response);
        if (total > maxBytes) return true;
        if (total > 0) return false;
        const contentLength = Number(response?.headers?.get('Content-Length') || 0);
        return response?.status === 206 && contentLength >= maxBytes;
    }

    async function readTextPreviewContent(response, maxBytes = TEXT_PREVIEW_MAX_BYTES) {
        let truncated = isTextResponseTruncated(response, maxBytes);
        const decoder = new TextDecoder('utf-8', { fatal: false });

        if (!response.body || typeof response.body.getReader !== 'function') {
            const blob = await response.blob();
            truncated = truncated || blob.size > maxBytes;
            const slice = truncated ? blob.slice(0, maxBytes) : blob;
            return {
                text: await slice.text(),
                truncated,
            };
        }

        const reader = response.body.getReader();
        let bytesRead = 0;
        let text = '';

        try {
            while (bytesRead < maxBytes) {
                const { done, value } = await reader.read();
                if (done) {
                    text += decoder.decode();
                    return { text, truncated };
                }

                const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
                const remaining = maxBytes - bytesRead;
                if (chunk.byteLength > remaining) {
                    text += decoder.decode(chunk.slice(0, remaining), { stream: true });
                    truncated = true;
                    await reader.cancel();
                    text += decoder.decode();
                    return { text, truncated };
                }

                text += decoder.decode(chunk, { stream: true });
                bytesRead += chunk.byteLength;
            }

            truncated = true;
            await reader.cancel();
            text += decoder.decode();
            return { text, truncated };
        } finally {
            try {
                reader.releaseLock?.();
            } catch (_) {
                // Older browsers may not expose releaseLock consistently.
            }
        }
    }

    function ensureSharedFilesPreview() {
        if (window.FilesPreview) {
            return window.FilesPreview;
        }

        const preview = {
            activeObjectUrl: null,
            activeFileId: null,
            activeFile: null,
            isOpen: false,
            previousFocus: null,
            supportedImageTypes: ['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/bmp', 'image/svg+xml', 'image/webp', 'image/avif'],
            supportedPdfTypes: ['application/pdf'],
            supportedTextTypes: ['text/plain', 'text/markdown', 'text/x-markdown', 'text/csv', 'text/html', 'text/css', 'application/json', 'text/json', 'application/javascript', 'text/javascript', 'application/xml', 'text/xml'],
            supportedAudioTypes: ['audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/wave', 'audio/x-wav', 'audio/ogg', 'audio/aac', 'audio/mp4', 'audio/m4a', 'audio/flac', 'audio/webm'],
            supportedVideoTypes: ['video/mp4', 'video/webm', 'video/ogg', 'video/quicktime'],

            ensureDom() {
                let backdrop = document.getElementById('filesPreviewBackdrop');
                if (!backdrop) {
                    backdrop = document.createElement('div');
                    backdrop.id = 'filesPreviewBackdrop';
                    backdrop.className = 'files-preview-backdrop';
                    backdrop.setAttribute('aria-hidden', 'true');
                    document.body.appendChild(backdrop);
                }

                let sidebar = document.getElementById('filesPreviewSidebar');
                if (!sidebar) {
                    sidebar = document.createElement('aside');
                    sidebar.id = 'filesPreviewSidebar';
                    sidebar.className = 'files-preview-sidebar';
                    sidebar.tabIndex = -1;
                    sidebar.setAttribute('role', 'dialog');
                    sidebar.setAttribute('aria-modal', 'true');
                    sidebar.setAttribute('aria-hidden', 'true');
                    sidebar.setAttribute('aria-labelledby', 'filesPreviewTitle');
                    sidebar.innerHTML = `
                        <div class="files-preview-drag-handle" id="filesPreviewDragHandle" aria-hidden="true"><div class="files-preview-drag-handle-bar"></div></div>
                        <div class="files-preview-resize-handle" id="filesPreviewResizeHandle" aria-hidden="true"></div>
                        <div class="main-container-header">
                            <div id="filesPreviewTitle">${escapeHtml(t('chat_share_file_preview_title', 'File preview'))}</div>
                            <div class="main-container-header-buttons">
                                <button type="button" class="om-button" id="filesSidebarPreviewDownload" aria-label="${escapeHtml(t('chat_share_file_download_aria', 'Download file'))}" title="${escapeHtml(t('chat_share_file_download_title', 'Download'))}">
                                    ${Icons.download}
                                </button>
                                <button type="button" class="om-button" id="filesSidebarPreviewClose" aria-label="${escapeHtml(t('chat_share_file_close_preview_aria', 'Close preview'))}" title="${escapeHtml(t('chat_share_file_close_title', 'Close'))}">
                                    ${Icons.close}
                                </button>
                            </div>
                        </div>
                        <div class="files-preview-content">
                            <div class="files-preview-body" id="filesPreviewBody"></div>
                        </div>
                    `;
                    document.body.appendChild(sidebar);
                }

                return {
                    backdrop,
                    sidebar,
                    title: document.getElementById('filesPreviewTitle'),
                    body: document.getElementById('filesPreviewBody'),
                    close: document.getElementById('filesSidebarPreviewClose'),
                    download: document.getElementById('filesSidebarPreviewDownload'),
                };
            },

            bindEvents() {
                const dom = preview.ensureDom();
                dom.close?.addEventListener('click', preview.close);
                dom.download?.addEventListener('click', preview.handleDownloadClick);
                dom.backdrop?.addEventListener('click', preview.close);
                document.addEventListener('keydown', preview.handleKeydown);
            },

            unbindEvents() {
                const dom = preview.ensureDom();
                dom.close?.removeEventListener('click', preview.close);
                dom.download?.removeEventListener('click', preview.handleDownloadClick);
                dom.backdrop?.removeEventListener('click', preview.close);
                document.removeEventListener('keydown', preview.handleKeydown);
            },

            handleKeydown(event) {
                if (!preview.isOpen) {
                    return;
                }
                const dom = preview.ensureDom();
                if (event.key === 'Escape') {
                    event.preventDefault();
                    preview.close();
                    return;
                }
                trapTabKey(event, dom.sidebar);
            },

            cleanupObjectUrl() {
                if (preview.activeObjectUrl) {
                    URL.revokeObjectURL(preview.activeObjectUrl);
                    preview.activeObjectUrl = null;
                }
            },

            async open(file) {
                const fileId = getFileId(file);
                if (!fileId) {
                    throw new Error(t('chat_share_file_missing_id', 'Missing file id'));
                }

                const dom = preview.ensureDom();
                const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
                preview.close();
                preview.previousFocus = opener && document.contains(opener) ? opener : null;
                const sidebarWasNewlyCreated = !dom.sidebar.dataset.previewInitialized;
                if (sidebarWasNewlyCreated) {
                    dom.sidebar.dataset.previewInitialized = 'true';
                    dom.sidebar.getBoundingClientRect();
                }
                preview.activeFileId = fileId;
                preview.activeFile = file;
                preview.isOpen = true;
                dom.title.textContent = getFileName(file);
                dom.title.title = getFileName(file);
                dom.body.innerHTML = `
                    <div class="files-preview-placeholder">
                        ${Icons.loading_circle}
                        <span>${escapeHtml(t('chat_share_file_preview_loading', 'Loading preview...'))}</span>
                    </div>
                `;
                const showPreviewShell = () => {
                    dom.sidebar.classList.add('open');
                    dom.sidebar.setAttribute('aria-hidden', 'false');
                    dom.backdrop.classList.add('active');
                    dom.backdrop.setAttribute('aria-hidden', 'false');
                };
                if (sidebarWasNewlyCreated) {
                    requestAnimationFrame(showPreviewShell);
                } else {
                    showPreviewShell();
                }
                preview.bindEvents();
                requestAnimationFrame(() => {
                    focusFirstElement(dom.sidebar, '#filesSidebarPreviewClose');
                });

                try {
                    const element = await preview.createPreviewElement(file);
                    if (!preview.isOpen || preview.activeFileId !== fileId) {
                        return;
                    }
                    dom.body.innerHTML = '';
                    dom.body.appendChild(element || preview.createUnsupportedPreview(file));
                } catch (error) {
                    console.error('Failed to open shared file preview', error);
                    dom.body.innerHTML = '';
                    dom.body.appendChild(preview.createErrorPreview(error));
                }
            },

            close() {
                const dom = preview.ensureDom();
                const wasOpen = preview.isOpen;
                const focusTarget = preview.previousFocus;
                preview.cleanupObjectUrl();
                preview.isOpen = false;
                preview.activeFileId = null;
                preview.activeFile = null;
                preview.previousFocus = null;
                dom.sidebar.classList.remove('open');
                dom.sidebar.setAttribute('aria-hidden', 'true');
                dom.backdrop.classList.remove('active');
                dom.backdrop.setAttribute('aria-hidden', 'true');
                preview.unbindEvents();
                setTimeout(() => {
                    if (!preview.isOpen) {
                        dom.body.innerHTML = '';
                    }
                }, 200);
                if (wasOpen) {
                    restoreFocus(focusTarget);
                }
            },

            async fetchFile(file, inline = true, options = {}) {
                const fileId = getFileId(file);
                const fetchFn = typeof window.chatFileFetch === 'function' ? window.chatFileFetch : window.fetch.bind(window);
                return fetchFn(sharedFileUrl(fileId, { inline }), {
                    ...(options || {}),
                    method: 'GET',
                    headers: {
                        ...((options && options.headers) || {}),
                    },
                });
            },

            async createPreviewElement(file) {
                const initialType = getFileType(file);
                const initialIsText = preview.supportedTextTypes.includes(initialType) || initialType.startsWith('text/');
                const initialIsImage = preview.supportedImageTypes.includes(initialType) || initialType.startsWith('image/');
                const initialIsPdf = preview.supportedPdfTypes.includes(initialType);
                const initialIsAudio = preview.supportedAudioTypes.includes(initialType) || initialType.startsWith('audio/');
                const initialIsVideo = preview.supportedVideoTypes.includes(initialType) || initialType.startsWith('video/');

                if ((initialIsImage || initialIsPdf || initialIsAudio || initialIsVideo)
                    && isFileOverPreviewLimit(file, BINARY_PREVIEW_MAX_BYTES)) {
                    return preview.createPreviewTooLarge(file);
                }

                const headers = initialIsText
                    ? { Range: `bytes=0-${TEXT_PREVIEW_MAX_BYTES - 1}` }
                    : {};
                let response = await preview.fetchFile(file, true, { headers });
                if (!response.ok) {
                    throw new Error(t('chat_share_file_preview_failed_status', 'Preview failed ({status})').replace('{status}', String(response.status)));
                }

                const responseType = normalizeMimeType(response.headers.get('Content-Type'));
                const fileType = responseType && responseType !== 'application/octet-stream'
                    ? responseType
                    : initialType;

                if (preview.supportedTextTypes.includes(fileType) || fileType.startsWith('text/')) {
                    const { text, truncated } = await readTextPreviewContent(response);
                    return preview.createTextPreview(text, truncated);
                }

                const isImage = preview.supportedImageTypes.includes(fileType) || fileType.startsWith('image/');
                const isPdf = preview.supportedPdfTypes.includes(fileType);
                const isAudio = preview.supportedAudioTypes.includes(fileType) || fileType.startsWith('audio/');
                const isVideo = preview.supportedVideoTypes.includes(fileType) || fileType.startsWith('video/');

                if (!preview.supportedTextTypes.includes(fileType) && !fileType.startsWith('text/') && headers.Range) {
                    cancelResponseBody(response);
                    if ((isImage || isPdf || isAudio || isVideo) && isFileOverPreviewLimit(file, BINARY_PREVIEW_MAX_BYTES)) {
                        return preview.createPreviewTooLarge(file);
                    }

                    response = await preview.fetchFile(file, true);
                    if (!response.ok) {
                        throw new Error(t('chat_share_file_preview_failed_status', 'Preview failed ({status})').replace('{status}', String(response.status)));
                    }
                }

                if (!isImage && !isPdf && !isAudio && !isVideo) {
                    cancelResponseBody(response);
                    return preview.createUnsupportedPreview(file);
                }

                if (isFileOverPreviewLimit(file, BINARY_PREVIEW_MAX_BYTES)) {
                    cancelResponseBody(response);
                    return preview.createPreviewTooLarge(file);
                }

                const blob = await response.blob();
                const objectUrl = URL.createObjectURL(blob);
                preview.activeObjectUrl = objectUrl;

                if (isImage) {
                    const container = document.createElement('div');
                    container.className = 'files-preview-image-container';
                    const img = document.createElement('img');
                    img.className = 'files-preview-image';
                    img.alt = getFileName(file);
                    img.src = objectUrl;
                    container.appendChild(img);
                    return container;
                }

                if (isPdf) {
                    const iframe = document.createElement('iframe');
                    iframe.className = 'files-preview-iframe';
                    iframe.title = getFileName(file);
                    iframe.src = objectUrl;
                    return iframe;
                }

                if (isAudio) {
                    const container = document.createElement('div');
                    container.className = 'files-preview-audio-container';
                    const audio = document.createElement('audio');
                    audio.className = 'files-preview-audio-player';
                    audio.controls = true;
                    audio.src = objectUrl;
                    container.appendChild(audio);
                    return container;
                }

                if (isVideo) {
                    const container = document.createElement('div');
                    container.className = 'files-preview-video-container';
                    const video = document.createElement('video');
                    video.className = 'files-preview-video-player';
                    video.controls = true;
                    video.playsInline = true;
                    video.src = objectUrl;
                    container.appendChild(video);
                    return container;
                }

                return preview.createUnsupportedPreview(file);
            },

            createTextPreview(content, truncated = false) {
                const pre = document.createElement('pre');
                pre.className = 'files-preview-text';
                pre.tabIndex = 0;
                pre.setAttribute('aria-label', t('chat_share_file_text_preview_aria', 'File text preview'));
                pre.textContent = typeof content === 'string' ? content : '';
                if (!truncated) {
                    return pre;
                }

                const wrapper = document.createElement('div');
                wrapper.className = 'files-preview-text-wrapper';

                const notice = document.createElement('div');
                notice.className = 'files-preview-text-notice';
                notice.setAttribute('role', 'note');
                notice.textContent = t(
                    'chat_share_file_text_preview_truncated',
                    'Preview truncated to the first {size}. Download the file to view everything.'
                ).replace('{size}', formatPreviewFileSize(TEXT_PREVIEW_MAX_BYTES));

                wrapper.appendChild(notice);
                wrapper.appendChild(pre);
                return wrapper;
            },

            createPreviewTooLarge(file) {
                const container = document.createElement('div');
                container.className = 'files-preview-unsupported';
                container.innerHTML = `
                    ${Icons.file}
                `;

                const text = document.createElement('div');
                text.className = 'files-preview-unsupported-text';
                text.textContent = t(
                    'chat_share_file_preview_too_large_limit',
                    'This file is too large to preview. Previewing is limited to {size}.'
                ).replace('{size}', formatPreviewFileSize(BINARY_PREVIEW_MAX_BYTES));

                const fileSize = getFileSize(file);
                if (fileSize) {
                    const size = document.createElement('span');
                    size.className = 'files-preview-size-note';
                    size.textContent = formatPreviewFileSize(fileSize);
                    text.appendChild(document.createElement('br'));
                    text.appendChild(size);
                }

                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'files-preview-download-btn';
                button.textContent = t('chat_share_file_download_button', 'Download file');
                button.addEventListener('click', preview.handleDownloadClick);

                container.appendChild(text);
                container.appendChild(button);
                return container;
            },

            createUnsupportedPreview(file) {
                const extension = getFileExtension(file).toUpperCase() || 'FILE';
                const fileSize = formatPreviewFileSize(file?.file_size || file?.meta?.file_size);
                const container = document.createElement('div');
                container.className = 'files-preview-unsupported';
                container.innerHTML = `
                    ${Icons.file}
                    <div class="files-preview-unsupported-text">
                        ${escapeHtml(t('chat_share_file_preview_unsupported', '{type} files cannot be previewed directly.')).replace('{type}', `<strong>${escapeHtml(extension)}</strong>`)}
                        ${fileSize ? `<span>${escapeHtml(fileSize)}</span>` : ''}
                    </div>
                `;
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'files-preview-download-btn';
                button.textContent = t('chat_share_file_download_button', 'Download file');
                button.addEventListener('click', preview.handleDownloadClick);
                container.appendChild(button);
                return container;
            },

            createErrorPreview(error) {
                const container = document.createElement('div');
                container.className = 'files-preview-placeholder';
                container.innerHTML = `
                    ${Icons.info}
                    <span>${escapeHtml(error?.message || t('chat_share_file_preview_failed', 'Failed to load preview'))}</span>
                `;
                return container;
            },

            async handleDownloadClick(event) {
                event?.preventDefault();
                const file = preview.activeFile;
                const fileId = getFileId(file);
                if (!fileId) {
                    return;
                }
                try {
                    const response = await preview.fetchFile(file, false);
                    if (!response.ok) {
                        throw new Error(t('chat_share_file_download_failed_status', 'Download failed ({status})').replace('{status}', String(response.status)));
                    }
                    const blob = await response.blob();
                    const url = URL.createObjectURL(blob);
                    const anchor = document.createElement('a');
                    anchor.href = url;
                    anchor.download = getFileName(file);
                    document.body.appendChild(anchor);
                    anchor.click();
                    document.body.removeChild(anchor);
                    URL.revokeObjectURL(url);
                } catch (error) {
                    console.error('Failed to download shared file', error);
                    window.notifyError?.(t('chat_share_file_download_failed', 'Failed to download file'));
                }
            },
        };

        window.FilesPreview = preview;
        return preview;
    }

    ensureSharedFilesPreview();

    function updateToken(payload) {
        state.shareAccessToken = payload?.share_access_token || null;
        state.shareAccessTokenExpiresAt = payload?.share_access_token_expires_at || null;
        if (payload?.access_mode) {
            state.accessMode = ['authenticated', 'invited'].includes(payload.access_mode) ? payload.access_mode : 'public';
        }
    }

    function currentShareRequiresUserAuth() {
        return state.accessMode === 'authenticated' || state.accessMode === 'invited';
    }

    function currentReturnPath() {
        return `${window.location.pathname}${window.location.search}${window.location.hash}`;
    }

    function redirectToLogin() {
        window.location.href = `/login?redirect=${encodeURIComponent(currentReturnPath())}`;
    }

    async function resolveUserAccessToken({ redirectOnMissing = false, forceRefresh = false } = {}) {
        if (!forceRefresh && state.userAccessToken) {
            return state.userAccessToken;
        }

        try {
            const response = await window.fetch('/api/v1/auth/refresh', {
                method: 'POST',
                credentials: 'include',
            });
            if (response.ok) {
                const payload = await response.json().catch(() => null);
                state.userAccessToken = payload?.access_token || null;
                if (state.userAccessToken) {
                    return state.userAccessToken;
                }
            }
        } catch (_) {
            state.userAccessToken = null;
        }

        state.userAccessToken = null;
        if (redirectOnMissing) {
            redirectToLogin();
        }
        return null;
    }

    function isAuthenticationRequired(payload) {
        return String(payload?.detail || '').trim().toLowerCase() === 'authentication required';
    }

    function buildAccessHeaders(includeUserAuth) {
        const headers = { 'Content-Type': 'application/json' };
        if (includeUserAuth && state.userAccessToken) {
            headers.Authorization = `Bearer ${state.userAccessToken}`;
        }
        return headers;
    }

    async function refreshAccessToken() {
        if (!state.shareId) {
            return;
        }
        const payload = await requestSharedAccess({ render: false });
        updateToken(payload);
    }

    window.chatFileFetch = async function chatFileFetch(url, options) {
        const requestOptions = {
            ...(options || {}),
            headers: {
                ...((options && options.headers) || {}),
            },
        };
        if (state.shareAccessToken) {
            requestOptions.headers.Authorization = `Bearer ${state.shareAccessToken}`;
        }
        if (currentShareRequiresUserAuth()) {
            const userToken = await resolveUserAccessToken({ redirectOnMissing: true });
            if (userToken) {
                requestOptions.headers['X-Omlorix-User-Authorization'] = `Bearer ${userToken}`;
            }
        }

        let response = await window.fetch(url, requestOptions);
        if (response.status === 401 && state.shareId) {
            try {
                if (currentShareRequiresUserAuth()) {
                    state.userAccessToken = null;
                    const userToken = await resolveUserAccessToken({ redirectOnMissing: true, forceRefresh: true });
                    if (userToken) {
                        requestOptions.headers['X-Omlorix-User-Authorization'] = `Bearer ${userToken}`;
                    }
                }
                await refreshAccessToken();
                if (state.shareAccessToken) {
                    requestOptions.headers.Authorization = `Bearer ${state.shareAccessToken}`;
                    response = await window.fetch(url, requestOptions);
                }
            } catch (_) {
                return response;
            }
        }
        return response;
    };

    function buildTranscriptSignature(messages) {
        try {
            return JSON.stringify(messages || []);
        } catch (_) {
            const safe = Array.isArray(messages) ? messages : [];
            const last = safe.length ? safe[safe.length - 1] : null;
            return `${safe.length}:${last?.id || ''}:${last?.created_at || ''}`;
        }
    }

    function renderTranscript(payload, { force = false } = {}) {
        const messages = Array.isArray(payload?.messages) ? payload.messages : [];
        state.lastTranscriptPayload = payload;
        if (payload?.updated_at) {
            state.lastUpdatedAt = payload.updated_at;
        }
        const signature = buildTranscriptSignature(messages);
        if (!force && signature === state.lastTranscriptSignature) {
            return;
        }

        state.lastTranscriptSignature = signature;

        const defaultTitle = t('chat_share_default_title', 'Shared Chat');
        if (sharedChatTitle) {
            sharedChatTitle.textContent = payload?.title || defaultTitle;
        }
        document.title = `${payload?.title || defaultTitle} · ${t('chat_share_document_title_suffix', 'Shared')}`;

        if (typeof window.renderChatTranscript === 'function') {
            window.renderChatTranscript(messages, {
                container: document.getElementById('chatAreaContainer'),
                clearContainer: true,
                trackAssistantVersions: false,
                readOnly: true,
            });
        }

        enhanceSharedChatKeyboardNavigation(document.getElementById('chatAreaContainer'));

        const chatArea = document.getElementById('chatArea');
        if (chatArea) {
            chatArea.scrollTop = chatArea.scrollHeight;
        }
    }

    async function requestSharedAccess({ render = true, password = state.password } = {}) {
        const body = {
            share_id: state.shareId,
        };
        if (password) {
            body.password = password;
        } else if (state.shareAccessToken) {
            body.share_access_token = state.shareAccessToken;
        }
        if (state.lastUpdatedAt) {
            body.known_updated_at = state.lastUpdatedAt;
        }

        if (currentShareRequiresUserAuth() && !state.userAccessToken) {
            await resolveUserAccessToken({ redirectOnMissing: true });
        }
        const shouldSendUserAuth = currentShareRequiresUserAuth() || Boolean(state.userAccessToken);

        let response = await window.fetch('/api/v1/chats/shared/access', {
            method: 'POST',
            headers: buildAccessHeaders(shouldSendUserAuth),
            body: JSON.stringify(body),
        });

        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }

        if (response.status === 401 && isAuthenticationRequired(payload)) {
            state.accessMode = 'authenticated';
            state.userAccessToken = null;
            const token = await resolveUserAccessToken({ redirectOnMissing: true, forceRefresh: true });
            if (token) {
                response = await window.fetch('/api/v1/chats/shared/access', {
                    method: 'POST',
                    headers: buildAccessHeaders(true),
                    body: JSON.stringify(body),
                });
                try {
                    payload = await response.json();
                } catch (_) {
                    payload = null;
                }
            }
        }

        if (response.status === 401) {
            if (isAuthenticationRequired(payload)) {
                const error = new Error(t('chat_share_auth_required', 'Sign in to view this shared chat.'));
                error.authRequired = true;
                throw error;
            }
            state.shareAccessToken = null;
            state.shareAccessTokenExpiresAt = null;
            const error = new Error(payload?.detail || t('chat_share_password_required', 'Password required'));
            error.passwordRequired = true;
            throw error;
        }

        if (!response.ok) {
            throw new Error(payload?.detail || t('chat_share_error_not_found', 'Shared chat not found'));
        }

        updateToken(payload);
        if (payload?.updated_at) {
            state.lastUpdatedAt = payload.updated_at;
        }
        if (render && !payload?.unchanged) {
            renderTranscript(payload);
        }

        return payload;
    }

    function startPolling() {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
        }
        state.pollTimer = setInterval(async () => {
            if (document.hidden) {
                return;
            }
            try {
                await requestSharedAccess({ render: true });
            } catch (error) {
                if (error?.passwordRequired) {
                    showView('password');
                    setPasswordError(t('chat_share_password_required_continue', 'Password required to continue'));
                    return;
                }
                if (error?.authRequired) {
                    return;
                }
                if (sharedErrorMessage) {
                    sharedErrorMessage.textContent = error?.message || t('chat_share_error_refresh_failed', 'Failed to refresh shared chat');
                }
                showView('error');
            }
        }, POLL_INTERVAL_MS);
    }

    async function initialLoad() {
        state.shareId = extractShareId();
        if (!state.shareId) {
            if (sharedErrorMessage) {
                sharedErrorMessage.textContent = t('chat_share_error_missing_share_id', 'Missing share identifier.');
            }
            showView('error');
            return;
        }

        try {
            localStorage.setItem('render_user_messages_markdown', 'true');
        } catch (error) {
            console.warn('Failed to save user messages markdown setting:', error);
        }
        try {
            localStorage.setItem('render_assistant_messages_markdown', 'true');
        } catch (error) {
            console.warn('Failed to save assistant messages markdown setting:', error);
        }

        showView('loading');
        try {
            await requestSharedAccess({ render: true });
            showView('chat');
            startPolling();
        } catch (error) {
            if (error?.passwordRequired) {
                setPasswordError('');
                showView('password');
                return;
            }
            if (error?.authRequired) {
                if (sharedErrorMessage) {
                    sharedErrorMessage.textContent = error?.message || t('chat_share_auth_required', 'Sign in to view this shared chat.');
                }
                showView('error');
                return;
            }
            if (sharedErrorMessage) {
                sharedErrorMessage.textContent = error?.message || t('chat_share_error_load_failed', 'Failed to load shared chat');
            }
            showView('error');
        }
    }

    function inferButtonLabel(button) {
        if (!button) {
            return '';
        }
        const existing = button.getAttribute('aria-label');
        if (existing) {
            return existing;
        }
        if (button.title) {
            return button.title;
        }
        if (button.classList.contains('copy-code-btn')) {
            return t('chat_share_copy_code_aria', 'Copy code');
        }
        if (button.classList.contains('download-code-btn')) {
            return t('chat_share_download_code_aria', 'Download code');
        }
        if (button.classList.contains('collapse-code-btn')) {
            return t('chat_share_collapse_code_aria', 'Collapse code block');
        }
        if (button.classList.contains('table-copy-btn')) {
            return t('chat_share_copy_table_aria', 'Copy table markdown');
        }
        return String(button.textContent || '').trim();
    }

    function makeScrollableRegionFocusable(element, label) {
        if (!(element instanceof HTMLElement)) {
            return;
        }
        const isScrollable = element.scrollWidth > element.clientWidth || element.scrollHeight > element.clientHeight;
        if (!isScrollable) {
            return;
        }
        if (!element.hasAttribute('tabindex')) {
            element.tabIndex = 0;
        }
        if (!element.hasAttribute('aria-label')) {
            element.setAttribute('aria-label', label);
        }
    }

    function enhanceSharedChatKeyboardNavigation(root = document) {
        if (!root || typeof root.querySelectorAll !== 'function') {
            return;
        }

        root.querySelectorAll('.markdown-body button').forEach((button) => {
            if (button instanceof HTMLButtonElement && !button.getAttribute('type')) {
                button.type = 'button';
            }
            const label = inferButtonLabel(button);
            if (label && !button.getAttribute('aria-label')) {
                button.setAttribute('aria-label', label);
            }
        });

        root.querySelectorAll('.markdown-body .table-wrapper').forEach((wrapper) => {
            requestAnimationFrame(() => makeScrollableRegionFocusable(wrapper, t('chat_share_scrollable_table_aria', 'Scrollable table')));
        });

        root.querySelectorAll('.markdown-body pre').forEach((pre) => {
            requestAnimationFrame(() => makeScrollableRegionFocusable(pre, t('chat_share_scrollable_code_aria', 'Scrollable code block')));
        });

        root.querySelectorAll('.inline-files-element-previewable, .assistant-inline-image-previewable').forEach((element) => {
            if (!element.hasAttribute('tabindex')) {
                element.setAttribute('tabindex', '0');
            }
            if (!element.hasAttribute('role')) {
                element.setAttribute('role', 'button');
            }
        });
    }

    function getActiveSharedModal() {
        const modal = document.querySelector('.code-block-preview-modal-overlay');
        if (!modal || modal.hidden || !isVisibleElement(modal)) {
            return null;
        }
        return modal;
    }

    function enhanceSharedPreviewModal(modal) {
        if (!(modal instanceof HTMLElement) || modal.dataset.shareKeyboardEnhanced === 'true') {
            return;
        }
        modal.dataset.shareKeyboardEnhanced = 'true';
        modal.tabIndex = -1;
        modal._sharePreviousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        requestAnimationFrame(() => {
            focusFirstElement(modal, '.code-block-preview-modal-close, .mermaid-preview-action, .visualizer-preview-action');
        });
    }

    function observeSharedPreviewModals() {
        if (typeof MutationObserver !== 'function') {
            return;
        }
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (!(node instanceof Element)) {
                        return;
                    }
                    if (node.classList.contains('code-block-preview-modal-overlay')) {
                        enhanceSharedPreviewModal(node);
                        return;
                    }
                    node.querySelectorAll?.('.code-block-preview-modal-overlay').forEach(enhanceSharedPreviewModal);
                });
                mutation.removedNodes.forEach((node) => {
                    if (!(node instanceof Element) || !node.classList.contains('code-block-preview-modal-overlay')) {
                        return;
                    }
                    restoreFocus(node._sharePreviousFocus);
                });
            });
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    function bindSharedKeyboardNavigation() {
        document.addEventListener('keydown', (event) => {
            const modal = getActiveSharedModal();
            if (modal) {
                trapTabKey(event, modal);
            }
        }, true);

        document.addEventListener('click', (event) => {
            const root = event.target instanceof Element
                ? event.target.closest('.markdown-body, #chatAreaContainer')
                : null;
            if (root) {
                requestAnimationFrame(() => enhanceSharedChatKeyboardNavigation(root));
            }
        }, true);

        observeSharedPreviewModals();
    }

    passwordForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const value = String(passwordInput?.value || '').trim();
        if (!value) {
            setPasswordError(t('chat_share_password_error_empty', 'Enter a password.'));
            passwordInput?.focus();
            return;
        }

        state.password = value;
        setPasswordError('');
        showView('loading');
        try {
            await requestSharedAccess({ render: true, password: value });
            setPasswordError('');
            state.password = null;
            if (passwordInput) {
                passwordInput.value = '';
            }
            showView('chat');
            startPolling();
        } catch (error) {
            if (error?.passwordRequired) {
                showView('password');
                setPasswordError(t('chat_share_password_error_invalid', 'Invalid password. Please try again.'));
                return;
            }
            if (error?.authRequired) {
                if (sharedErrorMessage) {
                    sharedErrorMessage.textContent = error?.message || t('chat_share_auth_required', 'Sign in to view this shared chat.');
                }
                showView('error');
                return;
            }
            if (sharedErrorMessage) {
                sharedErrorMessage.textContent = error?.message || t('chat_share_error_load_failed', 'Failed to load shared chat');
            }
            showView('error');
        }
    });

    passwordInput?.addEventListener('input', () => {
        if (passwordInput.getAttribute('aria-invalid') === 'true') {
            setPasswordError('');
        }
    });

    sharedRetryBtn?.addEventListener('click', () => {
        void initialLoad();
    });

    window.addEventListener('beforeunload', () => {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
        }
    });

    document.addEventListener('i18n:updated', () => {
        if (state.lastTranscriptPayload) {
            renderTranscript(state.lastTranscriptPayload, { force: true });
        }
    });

    bindSharedKeyboardNavigation();
    void initialLoad();
})();
