// DOM Helpers
// ============================================================================

const NotesDOM = {
    get workspace() { return document.getElementById('notesWorkspace'); },
    get sidebar() { return document.getElementById('notesSidebar'); },
    get sidebarList() { return document.getElementById('notesSidebarList'); },
    get sidebarTitle() { return document.querySelector('#notesSidebar .notes-sidebar-title'); },
    get sidebarSearch() { return document.getElementById('notesSidebarSearch'); },
    get addBtn() { return document.getElementById('notesSidebarAddBtn'); },
    get main() { return document.getElementById('notesMain'); },
    get emptyState() { return document.getElementById('notesEmptyState'); },
    get noNotesState() { return document.getElementById('notesNoNotesState'); },
    get editorView() { return document.getElementById('notesEditorView'); },
    get editorHeaderLeading() { return document.getElementById('notesEditorHeaderLeading'); },
    get markdownEditorHost() { return document.getElementById('notesMarkdownEditorHost'); },
    get markdownEditorControls() { return document.getElementById('notesMarkdownEditorControls'); },
    get markdownTab() { return document.getElementById('notesMarkdownTab'); },
    get editorTab() { return document.getElementById('notesEditorTab'); },
    get downloadFormat() { return document.getElementById('notesDownloadFormat'); },
    get downloadBtn() { return document.getElementById('notesDownloadBtn'); },
    get editorTextarea() { return document.getElementById('notesEditorTextarea'); },
    get preview() { return document.getElementById('notesPreview'); },
    get previewMeta() { return document.getElementById('notesPreviewMeta'); },
    get attachmentsStrip() { return document.getElementById('notesAttachmentsStrip'); },
    get saveStatus() { return document.getElementById('notesSaveStatus'); },
    get deleteOverlay() { return document.getElementById('notesDeleteOverlay'); },
    get restoreOverlay() { return document.getElementById('notesRestoreOverlay'); },
    get searchInput() { return document.getElementById('notesSidebarSearchInput'); },
    get searchClear() { return document.getElementById('notesSidebarSearchClear'); },
    get inlineUploadInput() { return document.getElementById('notesInlineUploadInput'); },
    get filePickerOverlay() { return document.getElementById('notesFilePickerOverlay'); },
    get filePickerSearch() { return document.getElementById('notesFilePickerSearch'); },
    get filePickerList() { return document.getElementById('notesFilePickerList'); },
    get filePickerEmpty() { return document.getElementById('notesFilePickerEmpty'); },
    get filePickerStatus() { return document.getElementById('notesFilePickerStatus'); },
    get filePickerConfirmBtn() { return document.getElementById('notesFilePickerConfirmBtn'); },
    get filePickerTitle() { return document.getElementById('notesFilePickerTitle'); },
    get filePickerSubtitle() { return document.getElementById('notesFilePickerSubtitle'); },
    get filePickerUploadInput() { return document.getElementById('notesFilePickerUploadInput'); },
    get recordingOverlay() { return document.getElementById('notesRecordingOverlay'); },
    get recordingStatus() { return document.getElementById('notesRecordingStatus'); },
    get recordingDetails() { return document.getElementById('notesRecordingDetails'); },
    get recordingTimer() { return document.getElementById('notesRecordingTimer'); },
    get recordingPreview() { return document.getElementById('notesRecordingPreview'); },
    get recordingPreviewName() { return document.getElementById('notesRecordingPreviewName'); },
    get recordingPreviewMeta() { return document.getElementById('notesRecordingPreviewMeta'); },
    get recordingPreviewAudio() { return document.getElementById('notesRecordingPreviewAudio'); },
    get recordingUseBtn() { return document.getElementById('notesRecordingUseBtn'); },
    get recordingPrimaryBtn() { return document.getElementById('notesRecordingPrimaryBtn'); },
};

const NOTE_FILE_TOKEN_REGEX = /\{\{note:(image|audio|file):([^:\|\}]+):([^|\}]+)(?:\|([^}]*?))?\}\}/g;

const NotesUtils = {
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = String(text ?? '');
        return div.innerHTML;
    },

    escapeMarkdownText(text) {
        return String(text ?? '').replace(/[[\]\\]/g, '').replace(/\r?\n/g, ' ').trim();
    },

    buildFileToken({ kind, ownerId, fileId, label }) {
        const safeKind = ['image', 'audio', 'file'].includes(kind) ? kind : 'file';
        const safeOwnerId = String(ownerId || '').trim();
        const safeFileId = String(fileId || '').trim();
        const safeLabel = String(label || '').replace(/\}\}/g, '').replace(/\r?\n/g, ' ').trim();
        if (!safeOwnerId || !safeFileId) return '';
        return `{{note:${safeKind}:${safeOwnerId}:${safeFileId}${safeLabel ? `|${safeLabel}` : ''}}}`;
    },

    buildAccessibleFileReference({ kind, file }) {
        const fileId = String(file?.file_id ?? file?.id ?? '').trim();
        if (!fileId) return '';
        const ownerId = this.getFileOwnerId(file);
        const label = this.getFileName(file) || fileId;
        const token = this.buildFileToken({ kind, ownerId, fileId, label });
        if (token) return token;
        // Shared-file list payloads intentionally omit another user's ID. The
        // ownerless scheme is resolved through the acting user's file access
        // and keeps replacement available without exposing that identity.
        const markdownLabel = this.escapeMarkdownText(label);
        const prefix = kind === 'image' ? '!' : '';
        return `${prefix}[${markdownLabel}](omlorix-file://${fileId})`;
    },

    parseFileTokens(content) {
        const matches = [];
        const source = String(content || '');
        source.replace(NOTE_FILE_TOKEN_REGEX, (rawToken, kind, ownerId, fileId, label) => {
            matches.push({
                rawToken,
                kind: String(kind || '').trim().toLowerCase(),
                owner_id: String(ownerId || '').trim(),
                file_id: String(fileId || '').trim(),
                label: String(label || '').trim(),
            });
            return rawToken;
        });
        return matches;
    },

    stripFileTokens(content) {
        return String(content || '').replace(NOTE_FILE_TOKEN_REGEX, '');
    },

    toPlainText(content) {
        return this.stripFileTokens(content)
            .replace(/!?\[([^\]]*)\]\(([^)]+)\)/g, '$1')
            .replace(/`{1,3}/g, '')
            .replace(/^\s{0,3}#{1,6}\s*/gm, '')
            .replace(/^\s*(?:[-*+]|\d+\.)\s+/gm, '')
            .replace(/^\s{0,3}>\s?/gm, '')
            .replace(/(\*\*|__|\*|_|~~)/g, '')
            .replace(/<[^>]+>/g, '')
            .replace(/\r\n?/g, '\n')
            .replace(/[ \t]+/g, ' ')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    },

    getFileName(file) {
        const meta = file?.meta && typeof file.meta === 'object' ? file.meta : {};
        return String(meta.original_filename || file?.file_name || file?.label || file?.file_id || '').trim();
    },

    getFileOwnerId(file) {
        return String(file?.user_id || file?.owner_id || '').trim();
    },

    getFileCategory(file) {
        const category = String(file?.file_category || '').trim().toLowerCase();
        if (category) return category;
        const type = String(file?.file_type || '').trim().toLowerCase();
        if (type.startsWith('image/')) return 'image';
        if (type.startsWith('audio/')) return 'audio';
        return 'document';
    },

    formatFileSize(size) {
        const bytes = Number(size);
        if (!Number.isFinite(bytes) || bytes <= 0) return '';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    },

    formatDuration(totalSeconds) {
        const safeSeconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
        const hours = Math.floor(safeSeconds / 3600);
        const minutes = Math.floor((safeSeconds % 3600) / 60);
        const seconds = safeSeconds % 60;
        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        }
        return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    },

    isInlineMediaFile(file) {
        const category = this.getFileCategory(file);
        return category === 'image' || category === 'audio';
    },

    buildNoteFileUrl(noteId, ownerId, fileId, inline = true) {
        const params = inline ? '?inline=true' : '';
        return `/api/v1/notes/${encodeURIComponent(noteId)}/files/${encodeURIComponent(ownerId)}/${encodeURIComponent(fileId)}${params}`;
    },

    sanitizeDownloadFilename(filename, fallback = 'note') {
        if (window.chatDownloadControls && typeof window.chatDownloadControls.sanitizeDownloadFilename === 'function') {
            return window.chatDownloadControls.sanitizeDownloadFilename(filename, fallback);
        }
        return String(filename || fallback || 'note')
            .trim()
            .slice(0, 180)
            .replace(/[\/:*?"<>|]/g, '-')
            .replace(/\s+/g, ' ') || fallback;
    },

    noteDownloadFilename(title, extension) {
        const safeExtension = String(extension || 'md').replace(/^\./, '').toLowerCase() || 'md';
        const safeTitle = this.sanitizeDownloadFilename(title || notesT('notes_accept_untitled', 'Untitled note'), 'note');
        const suffix = `.${safeExtension}`;
        if (safeTitle.toLowerCase().endsWith(suffix)) return safeTitle;
        return `${safeTitle.replace(/\.(md|markdown|pdf|txt)$/i, '') || 'note'}${suffix}`;
    },

    saveBlob(blob, filename) {
        if (window.chatDownloadControls && typeof window.chatDownloadControls.saveBlobAsFile === 'function') {
            window.chatDownloadControls.saveBlobAsFile(blob, filename);
            return;
        }
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = this.sanitizeDownloadFilename(filename, 'note');
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    },

    insertTextAtCursor(textarea, text, { selectInserted = false } = {}) {
        if (!textarea) return;
        const start = Number.isFinite(textarea.selectionStart) ? textarea.selectionStart : textarea.value.length;
        const end = Number.isFinite(textarea.selectionEnd) ? textarea.selectionEnd : textarea.value.length;
        const value = textarea.value || '';
        textarea.value = `${value.slice(0, start)}${text}${value.slice(end)}`;
        const cursor = start + text.length;
        textarea.focus();
        if (selectInserted) {
            textarea.setSelectionRange(start, cursor);
        } else {
            textarea.setSelectionRange(cursor, cursor);
        }
    },
};

const NotesPreview = {
    markdownRenderer: null,

    getRenderer() {
        if (this.markdownRenderer || typeof window.markdownit !== 'function') {
            return this.markdownRenderer;
        }
        const renderer = window.markdownit({
            html: true,
            linkify: true,
            typographer: true,
            breaks: true,
        });
        this.markdownRenderer = renderer;
        return renderer;
    },

    referencedFileMap(referencedFiles) {
        const map = new Map();
        (Array.isArray(referencedFiles) ? referencedFiles : []).forEach((file) => {
            const key = `${file.kind}:${file.owner_id}:${file.file_id}`;
            map.set(key, file);
        });
        return map;
    },

    fileIconSvg(kind) {
        if (kind === 'image') {
            return Icons.image_gen;
        }
        if (kind === 'audio') {
            return Icons.audio_gen;
        }
        return Icons.file;
    },

    expandFileTokens(content, noteId, referencedFiles) {
        const refMap = this.referencedFileMap(referencedFiles);
        return String(content || '').replace(NOTE_FILE_TOKEN_REGEX, (_, kind, ownerId, fileId, label) => {
            const key = `${kind}:${ownerId}:${fileId}`;
            const reference = refMap.get(key) || null;
            const rawDisplayName = String(label || reference?.label || reference?.file_name || notesT('notes_attached_file', 'Attached file')).trim();
            const displayName = NotesUtils.escapeHtml(rawDisplayName);
            const fileUrl = NotesUtils.buildNoteFileUrl(noteId, ownerId, fileId, true);

            if (reference && reference.available === false) {
                const missingText = notesFormatT('notes_missing_file', 'Missing {kind} file: {name}', {
                    kind,
                    name: rawDisplayName,
                });
                return `\n\n<div class="notes-preview-missing-file">${NotesUtils.escapeHtml(missingText)}</div>\n\n`;
            }

            if (kind === 'image') {
                return `\n\n![${NotesUtils.escapeMarkdownText(displayName)}](${fileUrl})\n\n`;
            }

            if (kind === 'audio') {
                return `\n\n<figure class="notes-inline-media"><audio class="notes-inline-audio-player" controls preload="metadata" src="${fileUrl}"></audio>${displayName ? `<figcaption>${displayName}</figcaption>` : ''}</figure>\n\n`;
            }

            const metaLabel = NotesUtils.escapeHtml(
                String(reference?.file_category || kind || 'file').trim().toLowerCase()
            );
            return `
                <div class="notes-embedded-file-card">
                    <div class="notes-embedded-file-meta">
                        <span class="notes-embedded-file-icon">${this.fileIconSvg(kind)}</span>
                        <div class="notes-embedded-file-copy">
                            <p class="notes-embedded-file-name">${displayName}</p>
                            <p class="notes-embedded-file-kind">${metaLabel}</p>
                        </div>
                    </div>
                    <a class="notes-embedded-file-link" href="${fileUrl}" target="_blank" rel="noopener noreferrer">
                        ${this.fileIconSvg('file')}
                        <span>${NotesUtils.escapeHtml(notesT('notes_embedded_file_open', 'Open'))}</span>
                    </a>
                </div>
            `;
        });
    },

    render(target, content, noteId, referencedFiles) {
        if (!target) return;
        target.classList.add('canvas-markdown-render');

        if (!noteId) {
            target.innerHTML = `<p class="notes-preview-placeholder">${NotesUtils.escapeHtml(notesT('notes_preview_select_note', 'Select a note to preview its content.'))}</p>`;
            return;
        }

        const trimmed = String(content || '').trim();
        if (!trimmed) {
            target.innerHTML = `<p class="notes-preview-placeholder">${NotesUtils.escapeHtml(notesT('notes_preview_empty_content', 'Nothing here yet. Start writing or insert media.'))}</p>`;
            return;
        }

        const expandedContent = this.expandFileTokens(content, noteId, referencedFiles);
        if (window.ChatMarkdownBlockEditor && typeof window.ChatMarkdownBlockEditor.renderMarkdownToHtml === 'function') {
            target.innerHTML = window.ChatMarkdownBlockEditor.renderMarkdownToHtml(expandedContent);
            return;
        }

        const renderer = this.getRenderer();
        if (!renderer) {
            target.textContent = trimmed;
            return;
        }

        const normalizedContent = window.ChatMarkdownUtils
            && typeof window.ChatMarkdownUtils.normalizeMarkdownForRender === 'function'
            ? window.ChatMarkdownUtils.normalizeMarkdownForRender(expandedContent)
            : expandedContent;
        const renderedHtml = renderer.render(normalizedContent);
        const preparedHtml = window.ChatMarkdownFileRefs
            && typeof window.ChatMarkdownFileRefs.prepareRenderedHtml === 'function'
            ? window.ChatMarkdownFileRefs.prepareRenderedHtml(renderedHtml)
            : renderedHtml;
        const sanitizer = window.ChatSanitizer;
        const sanitizedHtml = sanitizer && typeof sanitizer.sanitizeHtml === 'function'
            ? sanitizer.sanitizeHtml(preparedHtml)
            : preparedHtml;
        target.innerHTML = sanitizedHtml;
    },
};

// ============================================================================
