// Notes Manager
// ============================================================================

const NotesManager = {
    getCurrentEditorContent() {
        if (NotesState.markdownEditor && typeof NotesState.markdownEditor.getValue === 'function') {
            return NotesState.markdownEditor.getValue();
        }
        return String(NotesDOM.editorTextarea?.value ?? '');
    },

    getCurrentNoteTitle() {
        const currentNote = NotesState.notes.find((note) => note.id === NotesState.selectedNoteId);
        return currentNote?.title || this.extractTitle(this.getCurrentEditorContent());
    },

    setDownloadControlsEnabled(enabled) {
        const canDownload = Boolean(enabled)
            && Boolean(NotesState.selectedNoteId)
            && NotesState.currentNoteContent !== null
            && !NotesState.isLoadingContent
            && !NotesState.isDownloadingNote;
        if (window.chatDownloadControls && typeof window.chatDownloadControls.setDownloadControlsEnabled === 'function') {
            window.chatDownloadControls.setDownloadControlsEnabled({
                button: NotesDOM.downloadBtn,
                select: NotesDOM.downloadFormat,
                enabled: canDownload,
                disabledClass: 'disabled',
                manageTabIndex: false,
            });
            return;
        }

        if (NotesDOM.downloadBtn) {
            NotesDOM.downloadBtn.disabled = !canDownload;
            NotesDOM.downloadBtn.classList.toggle('disabled', !canDownload);
            NotesDOM.downloadBtn.setAttribute('aria-disabled', canDownload ? 'false' : 'true');
        }
        if (NotesDOM.downloadFormat) {
            NotesDOM.downloadFormat.disabled = !canDownload;
        }
    },

    setDownloadBusy(isBusy) {
        NotesState.isDownloadingNote = Boolean(isBusy);
        const canEnableAfterBusy = Boolean(NotesState.selectedNoteId) && !NotesState.isLoadingContent;

        if (window.chatDownloadControls && typeof window.chatDownloadControls.setDownloadBusy === 'function') {
            window.chatDownloadControls.setDownloadBusy({
                button: NotesDOM.downloadBtn,
                select: NotesDOM.downloadFormat,
                busy: Boolean(isBusy),
                enabled: canEnableAfterBusy,
                disabledClass: 'disabled',
                manageTabIndex: false,
                busyLabel: notesT('notes_download_preparing', 'Preparing download...'),
                idleLabel: notesT('notes_download_aria', 'Download note'),
            });
            return;
        }

        this.setDownloadControlsEnabled(canEnableAfterBusy && !isBusy);
    },

    updateDownloadControls() {
        this.setDownloadControlsEnabled(Boolean(NotesState.selectedNoteId) && NotesState.currentNoteContent !== null);
    },

    async downloadCurrentNote() {
        if (!NotesState.selectedNoteId || NotesState.isLoadingContent || NotesState.isDownloadingNote) return;

        const selectedNoteId = NotesState.selectedNoteId;
        const selectedFormat = typeof window.chatDownloadControls?.getSelectedDownloadFormat === 'function'
            ? window.chatDownloadControls.getSelectedDownloadFormat(NotesDOM.downloadFormat, 'md')
            : String(NotesDOM.downloadFormat?.value || 'md');
        const format = String(selectedFormat || 'md').toLowerCase() === 'pdf' ? 'pdf' : 'md';
        const title = this.getCurrentNoteTitle();

        try {
            this.setDownloadBusy(true);

            if (format === 'md') {
                const content = this.getCurrentEditorContent();
                const filename = NotesUtils.noteDownloadFilename(title, 'md');
                NotesUtils.saveBlob(new Blob([content], { type: 'text/markdown;charset=utf-8' }), filename);
                showNotification?.(notesT('notes_download_success', 'Note downloaded.'), 'success');
                return;
            }

            if (NotesState.hasUnsavedChanges && NotesState.canEditCurrentNote) {
                let saved = await this.saveCurrentNote(selectedNoteId);
                if (!saved && NotesState.isSaving) {
                    const settled = await waitForNoteSaveToSettle(() => NotesState.isSaving);
                    if (!settled) {
                        throw new Error(notesT('notes_error_save_note', 'Failed to save note'));
                    }
                    saved = NotesState.hasUnsavedChanges ? await this.saveCurrentNote(selectedNoteId) : true;
                }
                if (!saved) {
                    throw new Error(notesT('notes_error_save_note', 'Failed to save note'));
                }
            }

            const filename = NotesUtils.noteDownloadFilename(title, 'pdf');
            const blob = await NotesAPI.downloadNote(selectedNoteId, 'pdf');
            NotesUtils.saveBlob(blob, filename);
            showNotification?.(notesT('notes_download_success', 'Note downloaded.'), 'success');
        } catch (error) {
            console.error('Failed to download note:', error);
            showNotification?.(error?.message || notesT('notes_download_failed', 'Failed to prepare note download.'), 'error');
        } finally {
            this.setDownloadBusy(false);
        }
    },

    syncEditorMirror(content = null) {
        const textarea = NotesDOM.editorTextarea;
        if (!textarea) return;
        textarea.value = content === null ? this.getCurrentEditorContent() : String(content ?? '');
    },

    buildMarkdownEditorMoreActions() {
        const sharedIcons = (typeof Icons === 'object' ? Icons : globalThis.Icons) || {};
        return [
            {
                id: 'notes-add-file',
                label: notesT('notes_add_file', 'Add File'),
                iconHtml: sharedIcons.file || '',
                onSelect: () => this.openFilePicker('append'),
            },
            {
                id: 'notes-record-audio',
                label: notesT('notes_record_audio', 'Record Audio'),
                iconHtml: sharedIcons.microphone || sharedIcons.audio_gen || '',
                onSelect: () => this.openRecordingModal(),
            },
        ];
    },

    ensureMarkdownEditor({ content = '', editable = false, focus = false } = {}) {
        const host = NotesDOM.markdownEditorHost;
        const fallbackTextarea = NotesDOM.editorTextarea;
        const value = String(content ?? '');
        this.syncEditorMirror(value);

        if (!host || !window.ChatMarkdownBlockEditor || typeof window.ChatMarkdownBlockEditor.create !== 'function') {
            if (fallbackTextarea) {
                fallbackTextarea.hidden = false;
                fallbackTextarea.value = value;
                fallbackTextarea.readOnly = !editable;
                fallbackTextarea.disabled = false;
                fallbackTextarea.classList.toggle('readonly', !editable);
                if (focus && editable) fallbackTextarea.focus();
            }
            return;
        }

        if (fallbackTextarea) {
            fallbackTextarea.hidden = true;
            fallbackTextarea.value = value;
        }

        const activeNoteId = NotesState.selectedNoteId || null;
        const mustRecreate = !NotesState.markdownEditor
            || NotesState.markdownEditorEditable !== editable
            || NotesState.markdownEditorNoteId !== activeNoteId;
        if (mustRecreate) {
            if (NotesState.markdownEditor && typeof NotesState.markdownEditor.destroy === 'function') {
                NotesState.markdownEditor.destroy();
            }
            host.innerHTML = '';
            NotesState.markdownEditor = window.ChatMarkdownBlockEditor.create({
                value,
                editable,
                onChange: (nextValue) => this.handleMarkdownEditorChange(nextValue),
                onSave: () => this.saveCurrentNote(),
                onReferenceSelection: (selectionData) => addNoteSelectionToChatReferences({
                    selectionData,
                    noteId: NotesState.selectedNoteId,
                    title: NotesRender.getNoteTitle(this.getCurrentEditorContent(), 60),
                    source: 'notes workspace editor',
                }),
                onStateChange: (state) => this.updateEditorViewTabs(state),
                moreActions: this.buildMarkdownEditorMoreActions(),
                onSelectUploadedImage: () => this.openFilePicker('media'),
            });
            NotesState.markdownEditorEditable = editable;
            NotesState.markdownEditorNoteId = activeNoteId;
            if (NotesState.markdownEditor?.element) {
                NotesState.markdownEditor.element.classList.add('notes-markdown-editor', 'canvas-markdown-editor-host');
                host.appendChild(NotesState.markdownEditor.element);
            }
        } else if (typeof NotesState.markdownEditor.setValue === 'function') {
            NotesState.markdownEditor.setValue(value);
        }

        host.classList.toggle('is-readonly', !editable);
        this.updateEditorViewTabs(NotesState.markdownEditor?.getState?.());
        if (focus && editable) {
            requestAnimationFrame(() => NotesState.markdownEditor?.focus?.());
        }
    },

    setEditorLoading(isLoading) {
        NotesDOM.markdownEditorHost?.classList.toggle('is-loading', Boolean(isLoading));
    },

    setEditorTabDisabled(button, disabled) {
        if (!button) return;
        button.disabled = Boolean(disabled);
        button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        button.classList.toggle('is-disabled', Boolean(disabled));
    },

    updateEditorViewTabs(state = null) {
        const hasEditor = Boolean(NotesState.markdownEditor);
        const normalizedView = state?.view === 'source' ? 'source' : 'editor';
        NotesDOM.markdownEditorControls?.setAttribute('aria-disabled', hasEditor ? 'false' : 'true');

        if (NotesDOM.markdownTab) {
            NotesDOM.markdownTab.classList.toggle('active', normalizedView === 'source');
            NotesDOM.markdownTab.setAttribute('aria-selected', normalizedView === 'source' ? 'true' : 'false');
            this.setEditorTabDisabled(NotesDOM.markdownTab, !hasEditor);
        }
        if (NotesDOM.editorTab) {
            NotesDOM.editorTab.classList.toggle('active', normalizedView === 'editor');
            NotesDOM.editorTab.setAttribute('aria-selected', normalizedView === 'editor' ? 'true' : 'false');
            this.setEditorTabDisabled(NotesDOM.editorTab, !hasEditor);
        }
    },

    switchEditorView(view) {
        if (!NotesState.markdownEditor || typeof NotesState.markdownEditor.switchView !== 'function') return;
        NotesState.markdownEditor.switchView(view === 'source' || view === 'markdown' ? 'source' : 'editor');
        this.updateEditorViewTabs(NotesState.markdownEditor.getState?.());
    },

    handleMarkdownEditorChange(nextValue) {
        const content = String(nextValue ?? '');
        this.syncEditorMirror(content);
        this.handleEditorInput(content);
    },

    setCurrentEditorContent(content, { editable = NotesState.canEditCurrentNote, focus = false } = {}) {
        this.ensureMarkdownEditor({ content: String(content ?? ''), editable: Boolean(editable), focus });
        this.renderEmbeddedFilesUi(String(content ?? ''));
    },

    async init() {
        if (NotesState.initialized) return;

        // Notes uses the same compact split-button as Canvas: the format menu
        // is custom-rendered below the complete control, while the existing
        // select remains the source of truth for download behavior.
        window.chatDownloadControls?.enhanceDownloadFormatSelect?.(NotesDOM.downloadFormat, {
            downloadButton: NotesDOM.downloadBtn,
        });
        this.setupEventListeners();
        if (!this._beforeUnloadHandler) {
            this._beforeUnloadHandler = (event) => {
                if (!this.hasPendingEdits()) return;
                event.preventDefault();
                event.returnValue = '';
            };
            window.addEventListener('beforeunload', this._beforeUnloadHandler);
        }
        this.updateDownloadControls();
        NotesState.initialized = true;
    },

    setupEventListeners() {
        NotesDOM.downloadBtn?.addEventListener('click', () => this.downloadCurrentNote());

        // Sidebar list item clicks
        const sidebarList = NotesDOM.sidebarList;
        if (sidebarList) {
            sidebarList.addEventListener('click', (e) => {
                // Handle menu button click
                const menuBtn = e.target.closest('.notes-list-item-menu-btn');
                if (menuBtn) {
                    e.preventDefault();
                    e.stopPropagation();
                    const noteId = menuBtn.dataset.noteId;
                    this.toggleNoteDropdown(noteId);
                    return;
                }

                // Handle dropdown option click
                const dropdownOption = e.target.closest('.select-dropdown-button[data-action]');
                if (dropdownOption) {
                    e.preventDefault();
                    e.stopPropagation();
                    const action = dropdownOption.dataset.action;
                    const noteId = dropdownOption.dataset.noteId;
                    this.closeAllDropdowns();
                    if (action === 'delete') {
                        this.showDeleteNoteWarning(noteId);
                    } else if (action === 'share') {
                        const note = NotesState.notes.find((item) => item.id === noteId);
                        if (canManageNoteSharing(note)) {
                            this.showShareModal(noteId);
                        } else if (typeof notifyWarning === 'function') {
                            notifyWarning(notesT('notes_share_disabled_by_admin_notice', 'Group admin disabled note sharing.'));
                        } else if (typeof showNotification === 'function') {
                            showNotification(notesT('notes_share_disabled_by_admin_notice', 'Group admin disabled note sharing.'), 'warning');
                        }
                    } else if (action === 'unsubscribe') {
                        this.handleUnsubscribe(noteId);
                    } else if (action === 'share-disabled') {
                        if (typeof notifyWarning === 'function') {
                            notifyWarning(notesT('notes_share_disabled_by_admin_notice', 'Group admin disabled note sharing.'));
                        } else if (typeof showNotification === 'function') {
                            showNotification(notesT('notes_share_disabled_by_admin_notice', 'Group admin disabled note sharing.'), 'warning');
                        }
                    }
                    return;
                }

                // Handle the row's primary action. Native button keyboard
                // activation is delivered as a click without involving the
                // sibling options button.
                const selectBtn = e.target.closest('.notes-list-item-select-btn');
                if (selectBtn) {
                    const noteId = selectBtn.dataset.noteId;
                    this.selectNote(noteId);
                }
            });
        }

        // Close dropdowns when clicking outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.notes-list-item-menu-btn') && !e.target.closest('[data-note-dropdown]')) {
                this.closeAllDropdowns();
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && NotesDOM.filePickerOverlay && !NotesDOM.filePickerOverlay.hasAttribute('hidden')) {
                this.closeFilePicker();
                return;
            }
            if (e.key === 'Escape' && NotesDOM.recordingOverlay && !NotesDOM.recordingOverlay.hasAttribute('hidden')) {
                this.handleRecordingCancel();
                return;
            }
        });

        // Add note button
        const addNoteBtn = document.getElementById('notesSidebarAddBtn');
        if (addNoteBtn) {
            addNoteBtn.addEventListener('click', () => this.createNewNote());
        }
        // No-notes CTA button
        const noNotesCreateBtn = document.getElementById('notesNoNotesCreateBtn');
        if (noNotesCreateBtn) {
            noNotesCreateBtn.addEventListener('click', () => this.createNewNote());
        }

        NotesDOM.markdownTab?.addEventListener('click', () => this.switchEditorView('source'));
        NotesDOM.editorTab?.addEventListener('click', () => this.switchEditorView('editor'));
        this.updateEditorViewTabs(null);

        // Fallback textarea auto-save when the shared markdown editor is not available.
        const textarea = NotesDOM.editorTextarea;
        if (textarea) {
            textarea.addEventListener('input', () => this.handleEditorInput(textarea.value));
        }

        NotesDOM.inlineUploadInput?.addEventListener('change', async (event) => {
            const files = Array.from(event.target.files || []);
            event.target.value = '';
            if (files.length) {
                await this.handleUploadedFilesForNote(files, NotesState.filePickerMode || 'append');
            }
        });

        NotesDOM.filePickerOverlay?.addEventListener('click', (event) => {
            if (event.target === NotesDOM.filePickerOverlay) {
                this.closeFilePicker();
            }
        });
        document.getElementById('notesFilePickerCloseBtn')?.addEventListener('click', () => this.closeFilePicker());
        document.getElementById('notesFilePickerCancelBtn')?.addEventListener('click', () => this.closeFilePicker());
        document.getElementById('notesFilePickerUploadBtn')?.addEventListener('click', () => NotesDOM.filePickerUploadInput?.click());
        NotesDOM.filePickerSearch?.addEventListener('input', (event) => this.handleFilePickerSearch(event.target.value));
        NotesDOM.filePickerConfirmBtn?.addEventListener('click', () => this.confirmFilePickerSelection());
        NotesDOM.filePickerUploadInput?.addEventListener('change', async (event) => {
            const files = Array.from(event.target.files || []);
            event.target.value = '';
            if (files.length) {
                await this.handleUploadedFilesForNote(files, NotesState.filePickerMode || 'append');
            }
        });
        document.querySelectorAll('.notes-file-picker-filter').forEach((button) => {
            button.addEventListener('click', () => this.setFilePickerFilter(button.dataset.filter || 'all'));
        });

        NotesDOM.recordingOverlay?.addEventListener('click', (event) => {
            if (event.target === NotesDOM.recordingOverlay) {
                this.handleRecordingCancel();
            }
        });
        document.getElementById('notesRecordingCloseBtn')?.addEventListener('click', () => this.handleRecordingCancel());
        document.getElementById('notesRecordingCancelBtn')?.addEventListener('click', () => this.handleRecordingCancel());
        document.getElementById('notesRecordingPrimaryBtn')?.addEventListener('click', () => this.handleRecordingPrimaryAction());
        document.getElementById('notesRecordingUseBtn')?.addEventListener('click', () => this.addPendingRecordingToNote());
        document.querySelectorAll('.notes-recording-source-btn').forEach((button) => {
            button.addEventListener('click', () => this.setRecordingSource(button.dataset.source || 'microphone'));
        });

        // Delete overlay buttons
        const deleteOverlay = NotesDOM.deleteOverlay;
        if (deleteOverlay) {
            deleteOverlay.addEventListener('click', (e) => {
                if (e.target === deleteOverlay) this.hideDeleteOverlay();
            });
        }

        const deleteCancelBtn = document.getElementById('notesDeleteCancelBtn');
        if (deleteCancelBtn) {
            deleteCancelBtn.addEventListener('click', () => this.hideDeleteOverlay());
        }

        const deleteConfirmBtn = document.getElementById('notesDeleteConfirmBtn');
        if (deleteConfirmBtn) {
            deleteConfirmBtn.addEventListener('click', () => this.confirmDeleteNote());
        }

        // Restore overlay buttons
        const restoreOverlay = NotesDOM.restoreOverlay;
        if (restoreOverlay) {
            restoreOverlay.addEventListener('click', (e) => {
                if (e.target === restoreOverlay) this.hideRestoreConfirmation();
            });
        }

        const restoreCancelBtn = document.getElementById('notesRestoreCancelBtn');
        if (restoreCancelBtn) {
            restoreCancelBtn.addEventListener('click', () => this.hideRestoreConfirmation());
        }

        const restoreConfirmBtn = document.getElementById('notesRestoreConfirmBtn');
        if (restoreConfirmBtn) {
            restoreConfirmBtn.addEventListener('click', () => this.executeRestoreVersion());
        }

        // Mobile back button
        const mobileBackBtn = document.getElementById('notesMobileBackBtn');
        if (mobileBackBtn) {
            mobileBackBtn.addEventListener('click', async (e) => {
                e.preventDefault();
                if (!await this.ensureCurrentNoteSaved()) return;
                this.hideMobileContent();
            });
        }

        // Search input
        const searchInput = NotesDOM.searchInput;
        if (searchInput) {
            searchInput.addEventListener('input', (e) => this.handleSearchInput(e.target.value));
            searchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    this.clearSearch();
                    searchInput.blur();
                }
            });
        }

        // Search clear button
        const searchClear = NotesDOM.searchClear;
        if (searchClear) {
            searchClear.addEventListener('click', () => this.clearSearch());
        }

        // History button
        const historyBtn = document.getElementById('notesHistoryBtn');
        if (historyBtn) {
            historyBtn.addEventListener('click', () => this.showHistoryPanel());
        }
    },

    // ============================================================================
    // Embedded Files + Preview
    // ============================================================================

    ensureEditableForEmbeddedFiles() {
        if (!NotesState.selectedNoteId) {
            showNotification?.(notesT('notes_select_first_warning', 'Select a note first.'), 'warning');
            return false;
        }
        if (!NotesState.canEditCurrentNote) {
            showNotification?.(notesT('notes_view_only_warning', 'This note is view only.'), 'warning');
            return false;
        }
        return true;
    },

    upsertReferencedFiles(files, kindOverride = null) {
        const nextFiles = Array.isArray(files) ? files : [];
        const merged = new Map(
            (Array.isArray(NotesState.referencedFiles) ? NotesState.referencedFiles : []).map((file) => [
                `${file.kind}:${file.owner_id}:${file.file_id}`,
                file,
            ])
        );

        nextFiles.forEach((file) => {
            const ownerId = NotesUtils.getFileOwnerId(file);
            const fileId = String(file?.file_id ?? file?.id ?? '').trim();
            if (!ownerId || !fileId) return;
            const kind = kindOverride || NotesUtils.getFileCategory(file);
            merged.set(`${kind}:${ownerId}:${fileId}`, {
                owner_id: ownerId,
                file_id: fileId,
                kind,
                label: NotesUtils.getFileName(file),
                file_name: NotesUtils.getFileName(file),
                file_type: file?.file_type || '',
                file_category: NotesUtils.getFileCategory(file),
                file_size: file?.file_size || 0,
                available: true,
            });
        });

        NotesState.referencedFiles = Array.from(merged.values());
    },

    renderEmbeddedFilesUi(content = null) {
        const effectiveContent = typeof content === 'string'
            ? content
            : String(this.getCurrentEditorContent?.() ?? NotesDOM.editorTextarea?.value ?? NotesState.currentNoteContent ?? '');

        NotesPreview.render(NotesDOM.preview, effectiveContent, NotesState.selectedNoteId, NotesState.referencedFiles);

        const attachmentTokens = NotesUtils.parseFileTokens(effectiveContent).filter((token) => token.kind === 'file');
        if (!NotesDOM.attachmentsStrip) return;

        if (!attachmentTokens.length) {
            NotesDOM.attachmentsStrip.innerHTML = '';
            return;
        }

        const unique = new Map();
        attachmentTokens.forEach((token) => {
            const key = `${token.owner_id}:${token.file_id}`;
            if (!unique.has(key)) unique.set(key, token);
        });

        NotesDOM.attachmentsStrip.innerHTML = Array.from(unique.values()).map((token) => {
            const reference = (NotesState.referencedFiles || []).find((file) => (
                file.kind === 'file'
                && file.owner_id === token.owner_id
                && file.file_id === token.file_id
            ));
            const label = reference?.file_name || reference?.label || token.label || notesT('notes_attached_file', 'Attached file');
            return `
                <span class="notes-attachment-pill">
                    ${Icons.file}
                    <span>${NotesUtils.escapeHtml(label)}</span>
                </span>
            `;
        }).join('');
    },

    insertFilesIntoCurrentNote(files, mode = 'append') {
        if (!Array.isArray(files) || !files.length) return;

        if (mode === 'media') {
            const mediaTokens = files
                .filter((file) => NotesUtils.isInlineMediaFile(file))
                .map((file) => NotesUtils.buildFileToken({
                    kind: NotesUtils.getFileCategory(file),
                    ownerId: NotesUtils.getFileOwnerId(file),
                    fileId: String(file?.file_id ?? file?.id ?? '').trim(),
                    label: NotesUtils.getFileName(file),
                }))
                .filter(Boolean);

            if (!mediaTokens.length) {
                showNotification?.(notesT('notes_inline_media_only_warning', 'Only image and audio files can be inserted inline.'), 'warning');
                return;
            }

            const insertion = `\n${mediaTokens.join('\n\n')}\n`;
            if (NotesState.markdownEditor && typeof NotesState.markdownEditor.insertMarkdown === 'function') {
                NotesState.markdownEditor.insertMarkdown(insertion);
            } else {
                const textarea = NotesDOM.editorTextarea;
                NotesUtils.insertTextAtCursor(textarea, insertion);
            }
            this.upsertReferencedFiles(files);
        } else {
            const fileTokens = files.map((file) => NotesUtils.buildFileToken({
                kind: 'file',
                ownerId: NotesUtils.getFileOwnerId(file),
                fileId: String(file?.file_id ?? file?.id ?? '').trim(),
                label: NotesUtils.getFileName(file),
            })).filter(Boolean);

            if (!fileTokens.length) return;

            const currentValue = this.getCurrentEditorContent();
            const trimmed = currentValue.trimEnd();
            const attachmentsHeading = notesT('notes_attachments_heading', 'Attachments');
            const escapedHeading = attachmentsHeading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const hasAttachmentsHeading = new RegExp(`(^|\\n)#{1,6}\\s+${escapedHeading}\\s*$`, 'im').test(trimmed);
            const prefix = trimmed ? '\n\n' : '';
            const sectionPrefix = hasAttachmentsHeading ? '\n' : `${prefix}## ${attachmentsHeading}\n`;
            this.setCurrentEditorContent(`${trimmed}${sectionPrefix}${fileTokens.join('\n')}\n`, {
                editable: NotesState.canEditCurrentNote,
                focus: true,
            });
            this.upsertReferencedFiles(files, 'file');
        }

        this.handleEditorInput(this.getCurrentEditorContent());
    },

    async loadFilePickerFiles(search = '', { append = false } = {}) {
        if (append && (NotesState.filePickerLoadingMore || !NotesState.filePickerHasMore)) return;
        const normalizedSearch = String(search || '').trim();
        const offset = append ? NotesState.filePickerOffset : 0;
        const requestToken = append ? NotesState.filePickerRequestToken : Symbol('note-file-picker');
        if (!append) {
            NotesState.filePickerRequestToken = requestToken;
            NotesState.filePickerLoading = true;
            NotesState.filePickerOffset = 0;
            NotesState.filePickerHasMore = false;
        } else {
            NotesState.filePickerLoadingMore = true;
        }
        NotesState.filePickerError = '';
        this.renderFilePicker();
        try {
            const response = await NotesAPI.fetchWorkspaceFiles(normalizedSearch, offset);
            if (NotesState.filePickerRequestToken !== requestToken || NotesState.filePickerSearch.trim() !== normalizedSearch) return;
            const items = Array.isArray(response?.items) ? response.items : [];
            NotesState.filePickerFiles = append
                ? this.appendUniqueById(NotesState.filePickerFiles, items)
                : items;
            NotesState.filePickerOffset = offset + items.length;
            NotesState.filePickerHasMore = Boolean(response?.has_more);
            NotesState.filePickerTotal = Number(response?.total || NotesState.filePickerFiles.length) || 0;
            this.applyFilePickerFilter();
        } catch (error) {
            console.error('Failed to load note file picker files:', error);
            if (!append) {
                NotesState.filePickerFiles = [];
                NotesState.filePickerFilteredFiles = [];
            }
            NotesState.filePickerError = notesT('notes_error_fetch_uploaded_files_period', 'Failed to load uploaded files.');
        } finally {
            if (!append) NotesState.filePickerLoading = false;
            NotesState.filePickerLoadingMore = false;
            this.renderFilePicker();
        }
    },

    applyFilePickerFilter() {
        const filter = NotesState.filePickerFilter || 'all';
        const query = String(NotesState.filePickerSearch || '').trim().toLowerCase();
        const files = Array.isArray(NotesState.filePickerFiles) ? NotesState.filePickerFiles : [];

        NotesState.filePickerFilteredFiles = files.filter((file) => {
            const category = NotesUtils.getFileCategory(file);
            if (NotesState.filePickerMode === 'media' && !['image', 'audio'].includes(category)) {
                return false;
            }
            if (
                NotesState.filePickerMode === 'replace'
                && ['image', 'audio'].includes(NotesState.referenceIssue?.kind)
                && category !== NotesState.referenceIssue.kind
            ) {
                return false;
            }
            if (filter !== 'all' && category !== filter) {
                return false;
            }
            if (!query) return true;
            const haystack = [
                NotesUtils.getFileName(file),
                file?.file_type || '',
                category,
            ].join(' ').toLowerCase();
            return haystack.includes(query);
        });
    },

    renderFilePicker() {
        const list = NotesDOM.filePickerList;
        const status = NotesDOM.filePickerStatus;
        const empty = NotesDOM.filePickerEmpty;
        const confirmBtn = NotesDOM.filePickerConfirmBtn;
        if (!list || !status || !empty || !confirmBtn) return;

        const selectedCount = NotesState.filePickerSelection.size;
        confirmBtn.disabled = selectedCount === 0;
        if (NotesState.filePickerMode === 'replace') {
            confirmBtn.textContent = notesT('notes_reference_replace_selected', 'Replace reference');
        } else {
            confirmBtn.textContent = NotesState.filePickerMode === 'media'
                ? (selectedCount > 1 ? notesFormatT('notes_file_picker_insert_count', 'Insert {count} Media Items', { count: selectedCount }) : notesT('notes_file_picker_insert_selected', 'Insert Selected'))
                : (selectedCount > 1 ? notesFormatT('notes_file_picker_add_count', 'Add {count} Files', { count: selectedCount }) : notesT('notes_file_picker_add_selected', 'Add Selected'));
        }

        document.querySelectorAll('.notes-file-picker-filter').forEach((button) => {
            button.classList.toggle('active', (button.dataset.filter || 'all') === NotesState.filePickerFilter);
        });

        if (NotesState.filePickerLoading) {
            status.textContent = notesT('notes_file_picker_loading', 'Loading uploaded files...');
            list.innerHTML = '';
            empty.hidden = true;
            return;
        }

        if (NotesState.filePickerError) {
            status.textContent = NotesState.filePickerError;
            list.innerHTML = '';
            empty.hidden = true;
            return;
        }

        // Only claim that further matching files exist when the server page is
        // unfiltered by a local category/media view.
        const visibleFilesHaveMore = NotesState.filePickerHasMore
            && NotesState.filePickerFilter === 'all'
            && NotesState.filePickerMode !== 'media';
        const visibleFileCount = `${NotesState.filePickerFilteredFiles.length}${visibleFilesHaveMore ? '+' : ''}`;
        status.textContent = notesFormatT(
            NotesState.filePickerFilteredFiles.length === 1 && !visibleFilesHaveMore ? 'notes_file_picker_available_one' : 'notes_file_picker_available_other',
            NotesState.filePickerFilteredFiles.length === 1 && !visibleFilesHaveMore ? '{count} file available' : '{count} files available',
            { count: visibleFileCount }
        );

        if (!NotesState.filePickerFilteredFiles.length) {
            list.innerHTML = '';
            empty.hidden = false;
            this.setupFilePickerInfiniteScroll();
            return;
        }

        empty.hidden = true;
        list.innerHTML = NotesState.filePickerFilteredFiles.map((file) => {
            const fileId = String(file?.file_id ?? file?.id ?? '').trim();
            const category = NotesUtils.getFileCategory(file);
            const selected = NotesState.filePickerSelection.has(fileId);
            return `
                <button type="button" class="notes-file-picker-item${selected ? ' selected' : ''}" data-file-id="${fileId}">
                    <div class="notes-file-picker-item-top">
                        <span class="notes-file-picker-item-icon">${NotesPreview.fileIconSvg(category)}</span>
                        <div>
                            <p class="notes-file-picker-item-name">${NotesUtils.escapeHtml(NotesUtils.getFileName(file))}</p>
                            <p class="notes-file-picker-item-meta">${NotesUtils.escapeHtml(category)}${file?.file_size ? ` • ${NotesUtils.formatFileSize(file.file_size)}` : ''}</p>
                        </div>
                        <span class="notes-file-picker-item-check" aria-hidden="true"></span>
                    </div>
                </button>
            `;
        }).join('');
        this.setupFilePickerInfiniteScroll();

        list.querySelectorAll('.notes-file-picker-item').forEach((item) => {
            item.addEventListener('click', () => {
                const fileId = item.dataset.fileId || '';
                if (!fileId) return;
                if (NotesState.filePickerSelection.has(fileId)) {
                    NotesState.filePickerSelection.delete(fileId);
                } else {
                    if (NotesState.filePickerMode === 'replace') NotesState.filePickerSelection.clear();
                    NotesState.filePickerSelection.add(fileId);
                }
                this.renderFilePicker();
            });
        });
    },

    handleFilePickerSearch(value) {
        NotesState.filePickerSearch = String(value || '');
        if (NotesState.filePickerSearchTimer) window.clearTimeout(NotesState.filePickerSearchTimer);
        NotesState.filePickerSearchTimer = window.setTimeout(() => {
            NotesState.filePickerSearchTimer = null;
            this.loadFilePickerFiles(NotesState.filePickerSearch);
        }, 250);
    },

    setFilePickerFilter(filter) {
        NotesState.filePickerFilter = filter || 'all';
        this.applyFilePickerFilter();
        this.renderFilePicker();
    },

    async loadMoreFilePickerFiles() {
        await this.loadFilePickerFiles(NotesState.filePickerSearch, { append: true });
    },

    setupFilePickerInfiniteScroll() {
        this._filePickerInfiniteObserver?.disconnect();
        const list = NotesDOM.filePickerList;
        if (!list || !NotesState.filePickerHasMore || typeof IntersectionObserver !== 'function') return;
        const sentinel = document.createElement('div');
        sentinel.className = 'workspace-infinite-scroll-sentinel notes-file-picker-sentinel';
        sentinel.setAttribute('aria-hidden', 'true');
        list.appendChild(sentinel);
        this._filePickerInfiniteObserver = new IntersectionObserver((entries) => {
            if (entries[0]?.isIntersecting) this.loadMoreFilePickerFiles();
        }, { root: list, rootMargin: '160px', threshold: 0 });
        this._filePickerInfiniteObserver.observe(sentinel);
    },

    async openFilePicker(mode = 'append') {
        if (!this.ensureEditableForEmbeddedFiles()) return;

        NotesState.filePickerMode = mode;
        NotesState.filePickerSelection = new Set();
        NotesState.filePickerFilter = mode === 'media'
            ? 'image'
            : (mode === 'replace' && ['image', 'audio'].includes(NotesState.referenceIssue?.kind)
                ? NotesState.referenceIssue.kind
                : 'all');
        NotesState.filePickerSearch = '';
        NotesState.filePickerLastFocused = document.activeElement;

        if (NotesDOM.filePickerTitle) {
            NotesDOM.filePickerTitle.textContent = mode === 'replace'
                ? notesT('notes_reference_replace_title', 'Replace unavailable reference')
                : (mode === 'media'
                    ? notesT('notes_file_picker_insert_title', 'Insert Inline Media')
                    : notesT('notes_file_picker_add_title', 'Add Files'));
        }
        if (NotesDOM.filePickerSubtitle) {
            NotesDOM.filePickerSubtitle.textContent = mode === 'replace'
                ? notesT('notes_reference_replace_subtitle', 'Choose an accessible Workspace File to replace the blocked reference.')
                : (mode === 'media'
                    ? notesT('notes_file_picker_insert_subtitle', 'Choose images or audio files to insert between paragraphs.')
                    : notesT('notes_file_picker_add_subtitle', 'Select files to add as attachment cards at the bottom of this note.'));
        }
        if (NotesDOM.filePickerSearch) {
            NotesDOM.filePickerSearch.value = '';
        }

        NotesDOM.filePickerOverlay?.removeAttribute('hidden');
        NotesDOM.filePickerOverlay?.setAttribute('aria-hidden', 'false');

        await this.loadFilePickerFiles('');
        requestAnimationFrame(() => NotesDOM.filePickerSearch?.focus());
    },

    closeFilePicker() {
        NotesDOM.filePickerOverlay?.setAttribute('hidden', '');
        NotesDOM.filePickerOverlay?.setAttribute('aria-hidden', 'true');
        NotesState.filePickerMode = null;
        NotesState.filePickerSelection = new Set();
        NotesState.filePickerSearch = '';
        NotesState.filePickerFilter = 'all';
        NotesState.filePickerError = '';
        NotesState.filePickerHasMore = false;
        NotesState.filePickerOffset = 0;
        NotesState.filePickerRequestToken = null;
        if (NotesState.filePickerSearchTimer) window.clearTimeout(NotesState.filePickerSearchTimer);
        NotesState.filePickerSearchTimer = null;
        this._filePickerInfiniteObserver?.disconnect();
        NotesState.filePickerLastFocused?.focus?.();
        NotesState.filePickerLastFocused = null;
    },

    async confirmFilePickerSelection() {
        const selectedIds = Array.from(NotesState.filePickerSelection);
        if (!selectedIds.length) return;
        const selectedFiles = NotesState.filePickerFiles.filter((file) => selectedIds.includes(String(file?.file_id ?? file?.id ?? '').trim()));
        if (NotesState.filePickerMode === 'replace') {
            this.replaceUnavailableReferenceWithFile(selectedFiles[0]);
        } else {
            this.insertFilesIntoCurrentNote(selectedFiles, NotesState.filePickerMode || 'append');
        }
        this.closeFilePicker();
    },

    async handleUploadedFilesForNote(fileList, mode = 'append') {
        if (!this.ensureEditableForEmbeddedFiles()) return;
        const files = Array.isArray(fileList) ? fileList : [];
        if (!files.length) return;

        const uploadedIds = [];
        try {
            NotesDOM.filePickerStatus && (NotesDOM.filePickerStatus.textContent = notesFormatT(
                files.length === 1 ? 'notes_uploading_file_one' : 'notes_uploading_file_other',
                files.length === 1 ? 'Uploading {count} file...' : 'Uploading {count} files...',
                { count: files.length }
            ));
            for (const file of files) {
                const result = await NotesAPI.uploadFile(file);
                if (result?.file_id) {
                    uploadedIds.push(String(result.file_id));
                }
            }

            await this.loadFilePickerFiles('');
            const uploadedFiles = NotesState.filePickerFiles.filter((file) => uploadedIds.includes(String(file?.file_id ?? file?.id ?? '').trim()));
            if (mode === 'replace') {
                this.replaceUnavailableReferenceWithFile(uploadedFiles[0]);
            } else {
                this.insertFilesIntoCurrentNote(uploadedFiles, mode);
            }
            this.closeFilePicker();
            showNotification?.(notesFormatT(
                uploadedFiles.length === 1 ? 'notes_added_file_one' : 'notes_added_file_other',
                uploadedFiles.length === 1 ? '{count} file added to the note.' : '{count} files added to the note.',
                { count: uploadedFiles.length }
            ), 'success');
        } catch (error) {
            console.error('Failed to upload files for note:', error);
            showNotification?.(error?.message || notesT('notes_error_upload_files_for_note', 'Failed to upload files for this note.'), 'error');
            NotesState.filePickerError = error?.message || notesT('notes_error_upload_files', 'Failed to upload files.');
            this.renderFilePicker();
        }
    },

    // ============================================================================
    // Recording Methods
    // ============================================================================

    isRecordingSourceSupported(source) {
        if (source === 'screen') {
            return Boolean(
                typeof window !== 'undefined'
                && window.isSecureContext !== false
                && navigator.mediaDevices
                && typeof navigator.mediaDevices.getDisplayMedia === 'function'
                && typeof window.MediaRecorder === 'function'
                && typeof window.File === 'function'
            );
        }

        return Boolean(
            typeof window !== 'undefined'
            && window.isSecureContext !== false
            && navigator.mediaDevices
            && typeof navigator.mediaDevices.getUserMedia === 'function'
            && typeof window.MediaRecorder === 'function'
            && typeof window.File === 'function'
        );
    },

    getRecordingSourceConfig(source = NotesState.recordingSource) {
        if (source === 'screen') {
            return {
                readyStatus: notesT('notes_recording_screen_ready_status', 'Ready to capture screen audio'),
                readyDetails: notesT('notes_recording_screen_ready_details', 'Share a browser tab or screen with audio enabled to capture a meeting or playback as an audio note.'),
                recordingStatus: notesT('notes_recording_screen_recording_status', 'Capturing screen audio...'),
                recordingDetails: notesT('notes_recording_screen_recording_details', 'Keep the shared tab or screen open while the meeting audio plays.'),
                startLabel: notesT('notes_recording_screen_start', 'Start Capture'),
                redoLabel: notesT('notes_recording_screen_redo', 'Capture Again'),
                unsupportedMessage: notesT('notes_recording_screen_unsupported', 'Screen audio capture is not supported in this browser.'),
                emptyMessage: notesT('notes_recording_screen_empty', 'No screen audio was captured. Share a tab or screen with audio enabled and try again.'),
                successMessage: notesT('notes_recording_screen_success', 'Screen audio is ready to add to the note.'),
            };
        }

        return {
            readyStatus: notesT('notes_recording_status_ready', 'Ready to record'),
            readyDetails: notesT('notes_recording_microphone_ready_details', 'Use your microphone to capture a voice memo or a live meeting and insert it inline as audio.'),
            recordingStatus: notesT('notes_recording_microphone_recording_status', 'Recording microphone audio...'),
            recordingDetails: notesT('notes_recording_microphone_recording_details', 'Leave this window open while the meeting or voice note is being recorded.'),
            startLabel: notesT('notes_recording_start', 'Start Recording'),
            redoLabel: notesT('notes_recording_microphone_redo', 'Record Again'),
            unsupportedMessage: notesT('notes_recording_microphone_unsupported', 'Microphone recording is not supported in this browser.'),
            emptyMessage: notesT('notes_recording_microphone_empty', 'No audio was captured. Check your microphone and try again.'),
            successMessage: notesT('notes_recording_microphone_success', 'Recording is ready to add to the note.'),
        };
    },

    getPreferredRecordingMimeType() {
        if (typeof window.MediaRecorder?.isTypeSupported !== 'function') {
            return '';
        }
        const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/mpeg', 'audio/ogg'];
        return candidates.find((candidate) => window.MediaRecorder.isTypeSupported(candidate)) || '';
    },

    buildRecordingTimestamp() {
        const now = new Date();
        const part = (value) => String(value).padStart(2, '0');
        return `${now.getFullYear()}${part(now.getMonth() + 1)}${part(now.getDate())}-${part(now.getHours())}${part(now.getMinutes())}${part(now.getSeconds())}`;
    },

    getRecordingExtension(mimeType) {
        const normalized = String(mimeType || '').toLowerCase();
        if (normalized.includes('mp4')) return 'm4a';
        if (normalized.includes('mpeg')) return 'mp3';
        if (normalized.includes('wav')) return 'wav';
        if (normalized.includes('ogg')) return 'ogg';
        if (normalized.includes('opus')) return 'opus';
        return 'webm';
    },

    buildRecordingFileName(source, mimeType) {
        const prefix = source === 'screen' ? 'meeting-screen-audio' : 'note-audio-recording';
        return `${prefix}-${this.buildRecordingTimestamp()}.${this.getRecordingExtension(mimeType)}`;
    },

    clearRecordingTimer() {
        if (NotesState.recordingTimerId) {
            window.clearInterval(NotesState.recordingTimerId);
            NotesState.recordingTimerId = null;
        }
    },

    stopRecordingStream() {
        const stream = NotesState.recordingStream;
        if (!stream) return;
        stream.getTracks().forEach((track) => {
            try {
                track.stop();
            } catch (_) {}
        });
        NotesState.recordingStream = null;
    },

    updateRecordingTimer() {
        const timer = NotesDOM.recordingTimer;
        if (!timer) return;

        if (NotesState.recordingIsRecording && NotesState.recordingStartedAt) {
            const elapsedSeconds = Math.max(0, Math.floor((Date.now() - NotesState.recordingStartedAt) / 1000));
            timer.textContent = NotesUtils.formatDuration(elapsedSeconds);
            return;
        }

        const pendingDuration = Number(NotesState.recordingPendingFile?.noteRecordingDurationSeconds || 0);
        timer.textContent = NotesUtils.formatDuration(pendingDuration);
    },

    updateRecordingModalUi() {
        const source = NotesState.recordingSource;
        const config = this.getRecordingSourceConfig(source);
        const supported = this.isRecordingSourceSupported(source);
        const hasPending = Boolean(NotesState.recordingPendingFile);

        document.querySelectorAll('.notes-recording-source-btn').forEach((button) => {
            const buttonSource = button.dataset.source || 'microphone';
            const buttonSupported = this.isRecordingSourceSupported(buttonSource);
            const active = buttonSource === source;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
            button.disabled = NotesState.recordingIsRecording || NotesState.recordingIsUploading || !buttonSupported;
            if (!buttonSupported) {
                button.title = this.getRecordingSourceConfig(buttonSource).unsupportedMessage;
            } else {
                button.removeAttribute('title');
            }
        });

        if (NotesDOM.recordingStatus) {
            if (!supported) {
                NotesDOM.recordingStatus.textContent = notesT('notes_recording_unavailable', 'Unavailable');
            } else if (NotesState.recordingIsUploading) {
                NotesDOM.recordingStatus.textContent = notesT('notes_recording_adding_status', 'Adding recording to note...');
            } else if (NotesState.recordingIsRecording) {
                NotesDOM.recordingStatus.textContent = config.recordingStatus;
            } else if (hasPending) {
                NotesDOM.recordingStatus.textContent = notesT('notes_recording_ready_status', 'Recording ready');
            } else {
                NotesDOM.recordingStatus.textContent = config.readyStatus;
            }
        }

        if (NotesDOM.recordingDetails) {
            if (!supported) {
                NotesDOM.recordingDetails.textContent = config.unsupportedMessage;
            } else if (NotesState.recordingIsUploading) {
                NotesDOM.recordingDetails.textContent = notesT('notes_recording_uploading_details', 'Uploading the recording and inserting it inline into this note.');
            } else if (NotesState.recordingIsRecording) {
                NotesDOM.recordingDetails.textContent = config.recordingDetails;
            } else if (hasPending) {
                NotesDOM.recordingDetails.textContent = notesFormatT('notes_recording_pending_details', '{name} is ready to be added inline as audio.', {
                    name: NotesState.recordingPendingFile.name,
                });
            } else {
                NotesDOM.recordingDetails.textContent = config.readyDetails;
            }
        }

        if (NotesDOM.recordingPrimaryBtn) {
            NotesDOM.recordingPrimaryBtn.textContent = NotesState.recordingIsRecording
                ? notesT('notes_recording_stop', 'Stop Recording')
                : (hasPending ? config.redoLabel : config.startLabel);
            NotesDOM.recordingPrimaryBtn.disabled = NotesState.recordingIsUploading || !supported;
        }

        if (NotesDOM.recordingUseBtn) {
            NotesDOM.recordingUseBtn.disabled = !hasPending || NotesState.recordingIsRecording || NotesState.recordingIsUploading;
            NotesDOM.recordingUseBtn.textContent = NotesState.recordingIsUploading
                ? notesT('notes_recording_adding', 'Adding...')
                : notesT('notes_recording_add_to_note', 'Add to Note');
        }

        const cancelBtn = document.getElementById('notesRecordingCancelBtn');
        const closeBtn = document.getElementById('notesRecordingCloseBtn');
        if (cancelBtn) {
            cancelBtn.textContent = NotesState.recordingIsRecording || hasPending
                ? notesT('notes_recording_discard', 'Discard')
                : notesT('notes_share_cancel', 'Cancel');
            cancelBtn.disabled = NotesState.recordingIsUploading;
        }
        if (closeBtn) {
            closeBtn.disabled = NotesState.recordingIsUploading;
        }

        if (NotesDOM.recordingPreview) {
            NotesDOM.recordingPreview.hidden = !hasPending;
        }
        if (NotesDOM.recordingPreviewName) {
            NotesDOM.recordingPreviewName.textContent = hasPending ? NotesState.recordingPendingFile.name : '';
        }
        if (NotesDOM.recordingPreviewMeta) {
            const details = [];
            if (hasPending && Number.isFinite(NotesState.recordingPendingFile.size)) {
                const formattedSize = NotesUtils.formatFileSize(NotesState.recordingPendingFile.size);
                if (formattedSize) details.push(formattedSize);
            }
            if (hasPending) {
                details.push(source === 'screen'
                    ? notesT('notes_recording_source_screen_audio', 'Screen Audio')
                    : notesT('notes_recording_source_microphone', 'Microphone'));
            }
            const durationSeconds = Number(NotesState.recordingPendingFile?.noteRecordingDurationSeconds || 0);
            if (durationSeconds > 0) {
                details.push(NotesUtils.formatDuration(durationSeconds));
            }
            NotesDOM.recordingPreviewMeta.textContent = details.join(' • ');
        }
        if (NotesDOM.recordingPreviewAudio) {
            if (hasPending) {
                if (!NotesDOM.recordingPreviewAudio.dataset.objectUrl) {
                    const objectUrl = URL.createObjectURL(NotesState.recordingPendingFile);
                    NotesDOM.recordingPreviewAudio.src = objectUrl;
                    NotesDOM.recordingPreviewAudio.dataset.objectUrl = objectUrl;
                }
            } else {
                const existingUrl = NotesDOM.recordingPreviewAudio.dataset.objectUrl || '';
                if (existingUrl) {
                    URL.revokeObjectURL(existingUrl);
                }
                NotesDOM.recordingPreviewAudio.removeAttribute('src');
                NotesDOM.recordingPreviewAudio.load();
                delete NotesDOM.recordingPreviewAudio.dataset.objectUrl;
            }
        }

        this.updateRecordingTimer();
    },

    resetRecordingState({ keepPendingFile = false } = {}) {
        this.clearRecordingTimer();
        this.stopRecordingStream();
        NotesState.recordingMediaRecorder = null;
        NotesState.recordingChunks = [];
        NotesState.recordingMimeType = '';
        NotesState.recordingStartedAt = 0;
        NotesState.recordingIsRecording = false;
        NotesState.recordingDiscardOnStop = false;
        if (!keepPendingFile) {
            NotesState.recordingPendingFile = null;
            const existingUrl = NotesDOM.recordingPreviewAudio?.dataset?.objectUrl || '';
            if (existingUrl && NotesDOM.recordingPreviewAudio) {
                URL.revokeObjectURL(existingUrl);
                NotesDOM.recordingPreviewAudio.removeAttribute('src');
                NotesDOM.recordingPreviewAudio.load();
                delete NotesDOM.recordingPreviewAudio.dataset.objectUrl;
            }
        }
    },

    openRecordingModal() {
        if (!this.ensureEditableForEmbeddedFiles()) return;
        NotesState.recordingLastFocused = document.activeElement;
        NotesDOM.recordingOverlay?.removeAttribute('hidden');
        NotesDOM.recordingOverlay?.setAttribute('aria-hidden', 'false');
        this.updateRecordingModalUi();
        requestAnimationFrame(() => NotesDOM.recordingPrimaryBtn?.focus());
    },

    closeRecordingModal() {
        NotesDOM.recordingOverlay?.setAttribute('hidden', '');
        NotesDOM.recordingOverlay?.setAttribute('aria-hidden', 'true');
        NotesState.recordingLastFocused?.focus?.();
        NotesState.recordingLastFocused = null;
    },

    setRecordingSource(source) {
        const normalizedSource = source === 'screen' ? 'screen' : 'microphone';
        if (NotesState.recordingIsRecording || NotesState.recordingIsUploading) {
            return;
        }
        if (!this.isRecordingSourceSupported(normalizedSource)) {
            showNotification?.(this.getRecordingSourceConfig(normalizedSource).unsupportedMessage, 'warning');
            return;
        }
        NotesState.recordingSource = normalizedSource;
        NotesState.recordingPendingFile = null;
        this.updateRecordingModalUi();
    },

    handleRecordingPrimaryAction() {
        if (NotesState.recordingIsRecording) {
            this.stopRecording();
            return;
        }

        if (NotesState.recordingPendingFile) {
            NotesState.recordingPendingFile = null;
            this.updateRecordingModalUi();
        }

        this.startRecording();
    },

    handleRecordingCancel() {
        if (NotesState.recordingIsUploading) {
            return;
        }

        if (NotesState.recordingIsRecording) {
            this.stopRecording({ discard: true });
        } else {
            NotesState.recordingPendingFile = null;
        }

        this.updateRecordingModalUi();
        this.closeRecordingModal();
    },

    finalizeRecording({ source, mimeType, chunks, discard = false, durationSeconds = 0 } = {}) {
        const config = this.getRecordingSourceConfig(source);
        this.resetRecordingState({ keepPendingFile: true });

        if (discard) {
            NotesState.recordingPendingFile = null;
            this.updateRecordingModalUi();
            return;
        }

        const blob = new Blob(Array.isArray(chunks) ? chunks : [], { type: mimeType || 'audio/webm' });
        if (!blob.size) {
            NotesState.recordingPendingFile = null;
            showNotification?.(config.emptyMessage, 'warning');
            this.updateRecordingModalUi();
            return;
        }

        const finalFile = new File([blob], this.buildRecordingFileName(source, blob.type || mimeType), {
            type: blob.type || mimeType || 'audio/webm',
            lastModified: Date.now(),
        });
        finalFile.noteRecordingSource = source;
        finalFile.noteRecordingDurationSeconds = Math.max(0, Math.floor(durationSeconds || 0));
        NotesState.recordingPendingFile = finalFile;
        showNotification?.(config.successMessage, 'success');
        this.updateRecordingModalUi();
    },

    stopRecording({ discard = false } = {}) {
        if (!NotesState.recordingIsRecording) {
            if (discard) {
                NotesState.recordingPendingFile = null;
                this.updateRecordingModalUi();
            }
            return;
        }

        NotesState.recordingDiscardOnStop = Boolean(discard);
        const recorder = NotesState.recordingMediaRecorder;
        const source = NotesState.recordingSource;
        const durationSeconds = NotesState.recordingStartedAt
            ? (Date.now() - NotesState.recordingStartedAt) / 1000
            : 0;

        if (recorder && recorder.state !== 'inactive') {
            recorder.stop();
            return;
        }

        this.finalizeRecording({
            source,
            mimeType: NotesState.recordingMimeType,
            chunks: NotesState.recordingChunks,
            discard: NotesState.recordingDiscardOnStop,
            durationSeconds,
        });
    },

    async startRecording() {
        if (NotesState.recordingIsRecording || NotesState.recordingIsUploading) return;
        if (!this.ensureEditableForEmbeddedFiles()) return;

        const source = NotesState.recordingSource;
        if (!this.isRecordingSourceSupported(source)) {
            showNotification?.(this.getRecordingSourceConfig(source).unsupportedMessage, 'warning');
            return;
        }

        let inputStream = null;

        try {
            if (source === 'screen') {
                inputStream = await navigator.mediaDevices.getDisplayMedia({
                    video: true,
                    audio: true,
                });
            } else {
                inputStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            }

            const audioTracks = inputStream.getAudioTracks();
            if (!audioTracks.length) {
                throw new Error(this.getRecordingSourceConfig(source).emptyMessage);
            }

            const recordingStream = new MediaStream(audioTracks);
            const mimeType = this.getPreferredRecordingMimeType();
            const recorderOptions = mimeType ? { mimeType } : {};
            const mediaRecorder = new MediaRecorder(recordingStream, recorderOptions);

            NotesState.recordingStream = inputStream;
            NotesState.recordingMediaRecorder = mediaRecorder;
            NotesState.recordingChunks = [];
            NotesState.recordingMimeType = mediaRecorder.mimeType || mimeType || '';
            NotesState.recordingStartedAt = Date.now();
            NotesState.recordingIsRecording = true;
            NotesState.recordingDiscardOnStop = false;

            this.clearRecordingTimer();
            NotesState.recordingTimerId = window.setInterval(() => this.updateRecordingTimer(), 1000);
            this.updateRecordingModalUi();

            mediaRecorder.ondataavailable = (event) => {
                if (event.data && event.data.size > 0) {
                    NotesState.recordingChunks.push(event.data);
                }
            };

            mediaRecorder.onerror = (event) => {
                console.error('Note recorder error:', event.error);
                showNotification?.(notesT('notes_recording_failed', 'Recording failed. Please try again.'), 'error');
                this.stopRecording({ discard: true });
            };

            mediaRecorder.onstop = () => {
                this.finalizeRecording({
                    source,
                    mimeType: NotesState.recordingMimeType || mediaRecorder.mimeType || mimeType,
                    chunks: NotesState.recordingChunks,
                    discard: NotesState.recordingDiscardOnStop,
                    durationSeconds: NotesState.recordingStartedAt ? (Date.now() - NotesState.recordingStartedAt) / 1000 : 0,
                });
            };

            inputStream.getTracks().forEach((track) => {
                track.addEventListener('ended', () => {
                    if (!NotesState.recordingIsRecording) return;
                    this.stopRecording();
                }, { once: true });
            });

            mediaRecorder.start(1000);
        } catch (error) {
            if (inputStream) {
                inputStream.getTracks().forEach((track) => {
                    try {
                        track.stop();
                    } catch (_) {}
                });
            }
            console.error('Failed to start note recording:', error);
            this.resetRecordingState();
            this.updateRecordingModalUi();

            const name = String(error?.name || '');
            const message = String(error?.message || '').trim();
            const cancelled = name === 'AbortError' || name === 'NotAllowedError' || /cancel|permission denied/i.test(message);
            if (cancelled) {
                showNotification?.(source === 'screen'
                    ? notesT('notes_recording_screen_cancelled', 'Screen audio capture cancelled.')
                    : notesT('notes_recording_cancelled', 'Recording cancelled.'), 'warning');
                return;
            }
            showNotification?.(message || notesT('notes_recording_start_failed', 'Could not start recording. Please try again.'), 'error');
        }
    },

    async addPendingRecordingToNote() {
        if (!this.ensureEditableForEmbeddedFiles()) return;
        if (NotesState.recordingIsUploading || !NotesState.recordingPendingFile) return;

        NotesState.recordingIsUploading = true;
        this.updateRecordingModalUi();

        try {
            const uploadResult = await NotesAPI.uploadFile(NotesState.recordingPendingFile);
            const fileId = String(uploadResult?.file_id || '').trim();
            if (!fileId) {
                throw new Error(notesT('notes_recording_upload_missing_file', 'Recording upload completed without a file id.'));
            }

            const uploadedFile = await NotesAPI.fetchFile(fileId);
            this.insertFilesIntoCurrentNote([uploadedFile], 'media');
            this.closeRecordingModal();
            NotesState.recordingPendingFile = null;
            showNotification?.(notesT('notes_recording_added_success', 'Recording added to the note.'), 'success');
        } catch (error) {
            console.error('Failed to upload recording for note:', error);
            showNotification?.(error?.message || notesT('notes_recording_add_failed', 'Failed to add the recording to this note.'), 'error');
        } finally {
            NotesState.recordingIsUploading = false;
            this.updateRecordingModalUi();
        }
    },
};
