/**
 * Notes workspace search, lifecycle, sharing, and refresh methods.
 */

Object.assign(NotesManager, {
    // ============================================================================
    // Search Methods
    // ============================================================================

    handleSearchInput(query) {
        const trimmedQuery = query.trim();
        NotesState.searchQuery = trimmedQuery;
        
        // Update clear button visibility
        const searchClear = NotesDOM.searchClear;
        if (searchClear) {
            searchClear.classList.toggle('visible', trimmedQuery.length > 0);
        }
        
        NotesState.isSearching = trimmedQuery.length > 0;
        if (NotesState.searchTimer) window.clearTimeout(NotesState.searchTimer);
        NotesState.searchTimer = window.setTimeout(() => {
            NotesState.searchTimer = null;
            this.loadNotes({ query: trimmedQuery });
        }, 250);
    },

    renderSearchResults() {
        const sidebarList = NotesDOM.sidebarList;
        if (!sidebarList) return;
        
        const query = NotesState.searchQuery;
        const results = NotesState.notes;
        
        if (results.length === 0) {
            sidebarList.innerHTML = `
                <div class="notes-search-empty">
                    <div class="notes-search-empty-icon">
                        ${Icons.magnifyingGlass}
                    </div>
                    <p class="notes-search-empty-title" data-i18n="notes_search_no_results">${NotesRender.escapeHtml(notesT('notes_search_no_results', 'No results found'))}</p>
                    <p class="notes-search-empty-text">${NotesRender.escapeHtml(notesFormatT('notes_search_no_match', 'No notes match "{query}"', { query }))}</p>
                </div>
            `;
            return;
        }
        
        const resultsHeader = `
            <div class="notes-search-results-header">
                <span class="notes-search-results-count">${NotesRender.escapeHtml(notesFormatT(results.length === 1 && !NotesState.notesHasMore ? 'notes_search_result_count_one' : 'notes_search_result_count_other', results.length === 1 && !NotesState.notesHasMore ? '{count} result' : '{count} results', { count: `${results.length}${NotesState.notesHasMore ? '+' : ''}` }))}</span>
                <button type="button" class="notes-search-results-clear" id="notesSearchResultsClear">${NotesRender.escapeHtml(notesT('notes_search_clear_action', 'Clear'))}</button>
            </div>
        `;
        
        const resultsHtml = results
            .map(note => this.renderSearchResultItem(note, query))
            .join('');
        
        sidebarList.innerHTML = resultsHeader + resultsHtml;
        this.setupNotesInfiniteScroll();
        
        // Add event listener for clear button in results header
        const clearBtn = document.getElementById('notesSearchResultsClear');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => this.clearSearch());
        }
    },

    renderSearchResultItem(note, query) {
        const title = note.title || notesT('notes_accept_untitled', 'Untitled note');
        const preview = note.snippet || '';
        const dateStr = NotesRender.formatDate(note.updated_at);
        const isActive = note.id === NotesState.selectedNoteId;
        const isSubscribed = note.is_subscribed === true;
        
        // Highlight matching text
        const highlightedTitle = this.highlightMatches(title, query);
        const highlightedPreview = preview ? this.highlightMatches(preview, query) : `<span class="notes-preview-empty">${NotesRender.escapeHtml(notesT('notes_no_additional_text', 'No additional text'))}</span>`;
        
        const dropdownOptions = NotesRender.noteDropdownOptions(note, { includeSubscriberCount: false });
        
        let ownerBadge = '';
        if (isSubscribed) {
            const canEditBadge = note.share_type === 'collaborate' ? `<span class="notes-can-edit-badge">${NotesRender.escapeHtml(notesT('notes_share_can_edit_badge', 'can edit'))}</span>` : '';
            const shareTypeBadge = note.share_type === 'collaborate'
                ? notesT('notes_share_collab_badge', 'collab')
                : notesT('notes_share_live_badge', 'live');
            const ownerText = notesFormatT('notes_shared_by_owner', 'by {owner}', {
                owner: note.owner_name || notesT('notes_unknown_owner', 'Unknown'),
            });
            ownerBadge = `<span class="notes-subscribed-badge">${NotesRender.escapeHtml(ownerText)} <span class="notes-share-type-badge ${note.share_type}">${NotesRender.escapeHtml(shareTypeBadge)}</span>${canEditBadge}</span>`;
        }
        return `
            <div class="notes-list-item ${isActive ? 'active' : ''}${isSubscribed ? ' subscribed' : ''}" 
                 data-note-id="${note.id}" 
                 data-is-subscribed="${isSubscribed}">
                <button type="button" class="notes-list-item-select-btn" data-note-id="${note.id}"
                        aria-label="${NotesRender.escapeHtml(title)}" aria-pressed="${isActive}">
                    <span class="notes-list-item-content">
                        <span class="notes-list-item-title">${highlightedTitle}${ownerBadge}</span>
                        <span class="notes-list-item-preview">${highlightedPreview}</span>
                        <span class="notes-list-item-date">${dateStr}</span>
                    </span>
                </button>
                <button type="button" class="notes-list-item-menu-btn" data-note-id="${note.id}" aria-label="${NotesRender.escapeHtml(notesT('notes_options_aria', 'Note options'))}">
                    ${Icons.ellipsisVertical}
                </button>
                <div class="select-dropdown" data-note-dropdown data-note-id="${note.id}">
                    ${dropdownOptions}
                </div>
            </div>
        `;
    },

    highlightMatches(text, query) {
        if (!query || !text) return NotesRender.escapeHtml(text);
        
        const escapedText = NotesRender.escapeHtml(text);
        const escapedQuery = NotesRender.escapeHtml(query);
        const regex = new RegExp(`(${escapedQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
        
        return escapedText.replace(regex, '<span class="notes-search-highlight">$1</span>');
    },

    clearSearch() {
        NotesState.searchQuery = '';
        NotesState.searchResults = [];
        NotesState.isSearching = false;
        
        const searchInput = NotesDOM.searchInput;
        if (searchInput) {
            searchInput.value = '';
        }
        
        const searchClear = NotesDOM.searchClear;
        if (searchClear) {
            searchClear.classList.remove('visible');
        }
        
        if (NotesState.searchTimer) {
            window.clearTimeout(NotesState.searchTimer);
            NotesState.searchTimer = null;
        }
        this.loadNotes({ query: '' });
    },

    toggleNoteDropdown(noteId) {
        const dropdown = document.querySelector(`[data-note-dropdown][data-note-id="${noteId}"]`);
        if (!dropdown) return;

        const isOpen = dropdown.classList.contains('open');
        this.closeAllDropdowns();

        if (!isOpen) {
            const trigger = dropdown.parentElement?.querySelector('.notes-list-item-menu-btn');
            window.prepareDropdownOpeningAnimation?.(trigger, dropdown);
            dropdown.classList.add('open');
            NotesState.openDropdownNoteId = noteId;
        }
    },

    closeAllDropdowns() {
        document.querySelectorAll('[data-note-dropdown].open').forEach(d => d.classList.remove('open'));
        NotesState.openDropdownNoteId = null;
    },

    sortNotesState() {
        NotesState.notes.sort((a, b) => {
            const getTime = (note) => {
                const value = note.updated_at || note.created_at || 0;
                const date = value instanceof Date ? value : new Date(value);
                return date.getTime() || 0;
            };
            return getTime(b) - getTime(a);
        });
    },

    async loadNotes({ append = false, query = NotesState.searchQuery } = {}) {
        const sidebarList = NotesDOM.sidebarList;
        if (!sidebarList) return;

        if (append && (NotesState.notesLoadingMore || !NotesState.notesHasMore)) return;
        const normalizedQuery = String(query || '').trim();
        const offset = append ? NotesState.notesOffset : 0;
        const requestToken = append ? NotesState.notesRequestToken : Symbol('notes-list');
        if (!append) {
            NotesState.notesRequestToken = requestToken;
            NotesState.isLoadingNotes = true;
            NotesState.notesOffset = 0;
            NotesState.notesHasMore = false;
            sidebarList.innerHTML = NotesRender.sidebarSkeleton();
        } else {
            NotesState.notesLoadingMore = true;
            this.renderNotesLoadingSentinel();
        }

        try {
            const page = await NotesAPI.fetchNotes(offset, normalizedQuery, append ? NotesState.notesCursor : null);
            if (NotesState.notesRequestToken !== requestToken || NotesState.searchQuery !== normalizedQuery) return;
            NotesState.notes = append
                ? this.appendUniqueById(NotesState.notes, page.items)
                : page.items;
            NotesState.notesOffset = offset + page.items.length;
            NotesState.notesHasMore = page.hasMore;
            NotesState.notesCursor = page.nextCursor;
            NotesState.searchResults = NotesState.isSearching ? NotesState.notes : [];
            this.sortNotesState();
            this.renderCurrentNotesList();
        } catch (error) {
            console.error('Failed to load notes:', error);
            if (!append) {
                sidebarList.innerHTML = `
                    <div class="notes-list-empty" style="padding: 20px;">
                        <p class="notes-list-empty-text">${NotesRender.escapeHtml(notesT('notes_error_fetch_notes', 'Failed to fetch notes'))}</p>
                    </div>
                `;
            } else {
                this.renderCurrentNotesList();
            }
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_error_fetch_notes', 'Failed to fetch notes'), 'error');
            }
        } finally {
            if (!append) NotesState.isLoadingNotes = false;
            NotesState.notesLoadingMore = false;
        }
    },

    async loadMoreNotes() {
        await this.loadNotes({ append: true, query: NotesState.searchQuery });
    },

    appendUniqueById(existing, incoming) {
        const seen = new Set(existing.map(item => String(item?.id || item?.file_id || '')));
        return existing.concat((incoming || []).filter((item) => {
            const id = String(item?.id || item?.file_id || '');
            if (!id || seen.has(id)) return false;
            seen.add(id);
            return true;
        }));
    },

    renderCurrentNotesList() {
        if (NotesState.isSearching) this.renderSearchResults();
        else this.renderSidebarNotes();
    },

    setupNotesInfiniteScroll() {
        this._notesInfiniteObserver?.disconnect();
        const container = NotesDOM.sidebarList;
        if (!container || !NotesState.notesHasMore || typeof IntersectionObserver !== 'function') return;
        let sentinel = container.querySelector('[data-notes-scroll-sentinel]');
        if (!sentinel) {
            sentinel = document.createElement('div');
            sentinel.dataset.notesScrollSentinel = 'true';
            sentinel.className = 'workspace-infinite-scroll-sentinel';
            sentinel.setAttribute('aria-hidden', 'true');
            container.appendChild(sentinel);
        }
        this._notesInfiniteObserver = new IntersectionObserver((entries) => {
            if (entries[0]?.isIntersecting) this.loadMoreNotes();
        }, { root: container, rootMargin: '120px', threshold: 0 });
        this._notesInfiniteObserver.observe(sentinel);
    },

    renderNotesLoadingSentinel() {
        const sentinel = NotesDOM.sidebarList?.querySelector('[data-notes-scroll-sentinel]');
        sentinel?.classList.add('loading');
    },

    renderSidebarNotes() {
        const sidebarList = NotesDOM.sidebarList;
        if (!sidebarList) return;

        if (NotesState.notes.length === 0) {
            sidebarList.innerHTML = `
                <div class="notes-sidebar-empty">
                    <div class="notes-sidebar-empty-icon">
                        ${Icons.file}
                    </div>
                    <p class="notes-sidebar-empty-title">${NotesRender.escapeHtml(notesT('notes_sidebar_empty_title', 'No notes yet'))}</p>
                    <p class="notes-sidebar-empty-text">${NotesRender.escapeHtml(notesT('notes_sidebar_empty_text', 'Create your first note to get started'))}</p>
                    <button type="button" class="notes-sidebar-empty-btn project-chat-placeholder-action" id="notesSidebarEmptyAddBtn">
                        ${Icons.plus}
                        ${NotesRender.escapeHtml(notesT('notes_sidebar_empty_cta', 'Create Note'))}
                    </button>
                </div>
            `;
            // Add event listener for empty state button
            const emptyAddBtn = document.getElementById('notesSidebarEmptyAddBtn');
            if (emptyAddBtn) {
                emptyAddBtn.addEventListener('click', () => this.createNewNote());
            }
            this.showNoNotesState();
            return;
        }

        this.hideNoNotesState();
        sidebarList.innerHTML = NotesState.notes
            .map(note => NotesRender.noteItem(note, note.id === NotesState.selectedNoteId))
            .join('');
        this.setupNotesInfiniteScroll();
    },

    showNoNotesState() {
        const emptyState = NotesDOM.emptyState;
        const noNotesState = NotesDOM.noNotesState;
        const editorView = NotesDOM.editorView;
        
        if (emptyState) emptyState.style.display = 'none';
        if (editorView) editorView.style.display = 'none';
        if (noNotesState) noNotesState.style.display = 'flex';
        NotesState.currentNoteContent = null;
        NotesState.currentNoteUpdatedAt = '';
        NotesState.referencedFiles = [];
        this.setCurrentEditorContent('', { editable: false });
        this.renderEmbeddedFilesUi('');
        this.updateDownloadControls();
    },

    hideNoNotesState() {
        const noNotesState = NotesDOM.noNotesState;
        if (noNotesState) noNotesState.style.display = 'none';

        // Restore default empty state when no note is selected
        if (!NotesState.selectedNoteId) {
            const emptyState = NotesDOM.emptyState;
            const editorView = NotesDOM.editorView;
            
            if (emptyState) emptyState.style.display = 'flex';
            if (editorView) editorView.style.display = 'none';
        }
    },

    hasPendingEdits() {
        return Boolean(
            NotesState.selectedNoteId
            && NotesState.canEditCurrentNote
            && (NotesState.hasUnsavedChanges || NotesState.isSaving)
        );
    },

    isNavigationBypassed() {
        return NotesState.navigationBypass;
    },

    setNavigationBypass(value) {
        NotesState.navigationBypass = Boolean(value);
    },

    async ensureCurrentNoteSaved() {
        if (NotesState.saveTimer) {
            clearTimeout(NotesState.saveTimer);
            NotesState.saveTimer = null;
        }
        if (NotesState.isSaving) {
            const settled = await waitForNoteSaveToSettle(() => NotesState.isSaving);
            if (!settled) return false;
        }
        if (!NotesState.hasUnsavedChanges) return true;
        return this.saveCurrentNote();
    },

    requestWorkspaceExit(onSuccess) {
        void this.ensureCurrentNoteSaved().then((saved) => {
            if (!saved) return;
            onSuccess?.();
        });
    },

    applyResolvedContent(noteId, content, updated = null) {
        const normalizedContent = String(content ?? '');
        const noteIndex = NotesState.notes.findIndex((note) => String(note.id) === String(noteId));
        if (noteIndex >= 0) {
            NotesState.notes[noteIndex] = {
                ...NotesState.notes[noteIndex],
                title: this.extractTitle(normalizedContent),
                snippet: this.extractSnippet(normalizedContent),
                updated_at: updated?.updated_at || NotesState.notes[noteIndex].updated_at,
            };
            this.sortNotesState();
            this.renderCurrentNotesList();
        }
        if (String(NotesState.selectedNoteId) !== String(noteId)) return;
        NotesState.currentNoteContent = normalizedContent;
        NotesState.lastSavedContent = normalizedContent;
        NotesState.currentNoteUpdatedAt = normalizeNoteRevisionToken(updated?.updated_at);
        NotesState.referencedFiles = Array.isArray(updated?.referenced_files) ? updated.referenced_files : NotesState.referencedFiles;
        NotesState.hasUnsavedChanges = false;
        NotesState.remoteUpdate = null;
        this.setCurrentEditorContent(normalizedContent, { editable: NotesState.canEditCurrentNote });
        this.hideRemoteUpdateBanner();
        this.updateSaveStatus(NotesState.canEditCurrentNote ? 'saved' : 'readonly');
    },

    async openConflictRecovery({
        noteId = NotesState.selectedNoteId,
        baseContent = NotesState.lastSavedContent,
        localContent = this.getCurrentEditorContent(),
        baseRevision = NotesState.currentNoteUpdatedAt,
        serverSnapshot = null,
    } = {}) {
        if (!noteId || !window.NotesConflictManager) return false;
        NotesState.hasUnsavedChanges = true;
        this.updateSaveStatus('conflict');
        this.showRemoteUpdateBanner(serverSnapshot, { conflict: true });
        try {
            return await window.NotesConflictManager.open({
                noteId,
                baseContent,
                localContent,
                baseRevision,
                serverSnapshot,
                fetchLatest: (id) => NotesAPI.fetchNoteContent(id),
                save: (id, content, revision) => NotesAPI.updateNote(id, content, revision),
                onResolved: ({ content, updated, resolution }) => {
                    if (resolution === 'server' && !updated?.updated_at && serverSnapshot) updated = serverSnapshot;
                    this.applyResolvedContent(noteId, content, updated);
                },
                onDeferred: () => {
                    if (String(NotesState.selectedNoteId) === String(noteId)) {
                        this.updateSaveStatus('conflict');
                        this.showRemoteUpdateBanner(null, { conflict: true });
                    }
                },
            });
        } catch (error) {
            console.error('Failed to open note conflict recovery:', error);
            this.updateSaveStatus('error');
            showNotification?.(notesT('notes_conflict_load_failed', 'Could not load the latest note. Your draft remains available in the editor.'), 'error');
            return false;
        }
    },

    async restoreConflictDraft(noteId, serverSnapshot) {
        const recovery = await window.NotesConflictManager?.getRecovery?.(noteId);
        if (!recovery || String(recovery.localContent ?? '') === String(serverSnapshot?.content ?? '')) return false;
        NotesState.lastSavedContent = String(recovery.baseContent ?? '');
        NotesState.currentNoteUpdatedAt = normalizeNoteRevisionToken(recovery.baseRevision);
        NotesState.hasUnsavedChanges = true;
        this.setCurrentEditorContent(String(recovery.localContent ?? ''), {
            editable: NotesState.canEditCurrentNote,
        });
        await this.openConflictRecovery({
            noteId,
            baseContent: recovery.baseContent,
            localContent: recovery.localContent,
            baseRevision: recovery.baseRevision,
            serverSnapshot,
        });
        return true;
    },

    async selectNote(noteId) {
        // Stop any existing auto-refresh when switching notes
        this.stopAutoRefresh();
        
        // Save current note before switching
        if (NotesState.selectedNoteId && NotesState.hasUnsavedChanges) {
            const saved = await this.ensureCurrentNoteSaved();
            if (!saved) {
                this.startAutoRefresh(NotesState.selectedNoteId);
                return false;
            }
        }

        this.hideRemoteUpdateBanner();
        this.hideFileReferenceIssue();

        NotesState.selectedNoteId = noteId;
        const note = NotesState.notes.find(n => n.id === noteId);
        
        if (!note) return;

        // Update sidebar active state
        this.renderCurrentNotesList();

        // Show editor with loading state
        const emptyState = NotesDOM.emptyState;
        const noNotesState = NotesDOM.noNotesState;
        const editorView = NotesDOM.editorView;

        if (emptyState) emptyState.style.display = 'none';
        if (noNotesState) noNotesState.style.display = 'none';
        if (editorView) editorView.style.display = 'flex';
        
        // Show loading state in editor
        NotesState.currentNoteContent = null;
        NotesState.currentNoteUpdatedAt = '';
        this.setCurrentEditorContent('', { editable: false });
        this.setEditorLoading(true);
        NotesState.isLoadingContent = true;
        this.updateDownloadControls();
        this.updateSaveStatus('loading');

        // Show content view on mobile
        this.showMobileContent();

        try {
            // Fetch full note content from API
            const contentData = await NotesAPI.fetchNoteContent(noteId);
            
            // Verify we're still on the same note
            if (NotesState.selectedNoteId !== noteId) return;
            
            NotesState.currentNoteContent = contentData.content;
            NotesState.referencedFiles = Array.isArray(contentData.referenced_files) ? contentData.referenced_files : [];
            NotesState.isLoadingContent = false;
            this.setEditorLoading(false);

            // Store edit permission in state before mounting so the shared
            // editor is created with the correct toolbar and source controls.
            const canEdit = contentData.share_type !== 'live';
            NotesState.canEditCurrentNote = canEdit;
            NotesState.lastSavedContent = contentData.content || '';
            NotesState.currentNoteUpdatedAt = normalizeNoteRevisionToken(contentData.updated_at);
            NotesState.hasUnsavedChanges = false;
            this.setCurrentEditorContent(contentData.content || '', {
                editable: canEdit,
                focus: canEdit,
            });

            // Show/hide read-only indicator
            this.updateReadOnlyIndicator(!canEdit, note.owner_name);

            this.renderEmbeddedFilesUi(contentData.content || '');

            this.updateSaveStatus(NotesState.canEditCurrentNote ? 'saved' : 'readonly');
            this.updateDownloadControls();

            const restoredConflictDraft = canEdit
                ? await this.restoreConflictDraft(noteId, contentData)
                : false;

            // Start auto-refresh for shared notes
            this.startAutoRefresh(noteId);
            if (restoredConflictDraft) return true;
            
        } catch (error) {
            console.error('Failed to load note content:', error);
            NotesState.isLoadingContent = false;
            this.setEditorLoading(false);
            this.setCurrentEditorContent(notesT('notes_error_fetch_note_content', 'Failed to fetch note content'), { editable: false });
            NotesState.referencedFiles = [];
            this.renderEmbeddedFilesUi('');
            this.updateSaveStatus('error');
            this.updateDownloadControls();
            
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_error_fetch_note_content', 'Failed to fetch note content'), 'error');
            }
        }
        return true;
    },
    
    updateReadOnlyIndicator(isReadOnly, ownerName = null) {
        let indicator = document.getElementById('notesReadOnlyIndicator');
        
        if (!isReadOnly) {
            if (indicator) indicator.style.display = 'none';
            return;
        }
        
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'notesReadOnlyIndicator';
            indicator.className = 'notes-readonly-indicator';
            // Ownership details are document status, so keep them with the
            // save/read-only state on the left instead of the action controls.
            const statusGroup = NotesDOM.editorHeaderLeading;
            if (statusGroup) {
                statusGroup.appendChild(indicator);
            }
        }
        
        indicator.innerHTML = `
            ${Icons.eye}
            <span>${notesT('notes_shared_view_only', 'View only')}${ownerName ? ` • ${notesFormatT('notes_shared_by', 'Shared by {owner}', { owner: ownerName })}` : ''}</span>
        `;
        indicator.style.display = 'flex';
    },

    showMobileContent() {
        if (window.innerWidth <= 768) {
            const workspace = NotesDOM.workspace;
            if (workspace) {
                workspace.classList.add('show-content');
            }
        }
    },

    hideMobileContent() {
        const workspace = NotesDOM.workspace;
        if (workspace) {
            workspace.classList.remove('show-content');
        }
        // Clear selection when going back on mobile
        if (window.innerWidth <= 768) {
            NotesState.selectedNoteId = null;
            NotesState.currentNoteContent = null;
            NotesState.currentNoteUpdatedAt = '';
            NotesState.referencedFiles = [];
            this.renderCurrentNotesList();
            // Hide editor, show empty state
            const emptyState = NotesDOM.emptyState;
            const editorView = NotesDOM.editorView;
            if (emptyState && NotesState.notes.length > 0) emptyState.style.display = 'flex';
            if (editorView) editorView.style.display = 'none';
            this.setCurrentEditorContent('', { editable: false });
            this.renderEmbeddedFilesUi('');
            this.updateDownloadControls();
        }
    },

    async createNewNote() {
        try {
            // Save current note before creating new one
            if (NotesState.selectedNoteId && NotesState.hasUnsavedChanges) {
                const saved = await this.ensureCurrentNoteSaved();
                if (!saved) return false;
            }

            const newNote = await NotesAPI.createNote('');
            // Convert NoteResponse to NoteListItem format for the list
            const listItem = {
                id: newNote.id,
                user_id: newNote.user_id,
                title: notesT('notes_accept_untitled', 'Untitled note'),
                snippet: '',
                clone_share_id: newNote.clone_share_id,
                live_share_id: newNote.live_share_id,
                collaborate_share_id: newNote.collaborate_share_id,
                created_at: newNote.created_at,
                updated_at: newNote.updated_at,
                is_subscribed: false,
                subscriber_count: null,
            };
            NotesState.notes.push(listItem);
            this.sortNotesState();
            this.renderCurrentNotesList();
            await this.selectNote(newNote.id);

            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_created_success', 'Note created'), 'success');
            }
        } catch (error) {
            console.error('Failed to create note:', error);
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_error_create_note', 'Failed to create note'), 'error');
            }
        }
    },

    handleEditorInput(nextContent = null) {
        if (!NotesState.selectedNoteId) return;
        
        // Don't process input for read-only notes
        if (!NotesState.canEditCurrentNote) return;

        const currentContent = nextContent === null ? this.getCurrentEditorContent() : String(nextContent ?? '');
        this.syncEditorMirror(currentContent);
        this.renderEmbeddedFilesUi(currentContent);
        if (
            NotesState.referenceIssue?.raw_token
            && !currentContent.includes(NotesState.referenceIssue.raw_token)
        ) {
            this.hideFileReferenceIssue();
        }

        // Once a stale write is known, keep the recovery snapshot current and
        // wait for an explicit merge decision instead of generating a stream of
        // guaranteed 409 retries from the autosave timer.
        if (window.NotesConflictManager?.isActiveFor?.(NotesState.selectedNoteId)) {
            NotesState.hasUnsavedChanges = true;
            window.NotesConflictManager.updateLocalDraft(NotesState.selectedNoteId, currentContent);
            this.updateSidebarPreview(NotesState.selectedNoteId, currentContent);
            this.updateSaveStatus('conflict');
            this.showRemoteUpdateBanner(null, { conflict: true });
            return;
        }
        
        // Check if content actually changed
        if (currentContent === NotesState.lastSavedContent) {
            NotesState.hasUnsavedChanges = false;
            this.updateSaveStatus('saved');
            return;
        }

        NotesState.hasUnsavedChanges = true;
        this.updateSaveStatus('saving');

        // Update sidebar preview immediately
        this.updateSidebarPreview(NotesState.selectedNoteId, currentContent);

        // Clear existing timer
        if (NotesState.saveTimer) {
            clearTimeout(NotesState.saveTimer);
        }

        // Set new timer for auto-save
        NotesState.saveTimer = setTimeout(() => {
            this.saveCurrentNote();
        }, AUTOSAVE_DELAY);
    },

    updateSidebarPreview(noteId, content) {
        const noteItem = document.querySelector(`.notes-list-item[data-note-id="${noteId}"]`);
        if (!noteItem) return;

        const titleEl = noteItem.querySelector('.notes-list-item-title');
        const previewEl = noteItem.querySelector('.notes-list-item-preview');

        if (titleEl) {
            const title = this.extractTitle(content);
            titleEl.childNodes[0].textContent = title;
            noteItem.querySelector('.notes-list-item-select-btn')?.setAttribute('aria-label', title);
        }
        if (previewEl) {
            const snippet = this.extractSnippet(content);
            previewEl.innerHTML = snippet ? NotesRender.escapeHtml(snippet) : `<span class="notes-preview-empty">${NotesRender.escapeHtml(notesT('notes_no_additional_text', 'No additional text'))}</span>`;
        }
    },

    getFileReferenceIssueFromError(error) {
        return normalizeNoteFileReferenceIssue(error);
    },

    ensureFileReferenceIssueBanner() {
        const editorShell = NotesDOM.editorView?.querySelector('.notes-editor-shell');
        if (!editorShell) return null;
        let banner = document.getElementById('notesFileReferenceIssue');
        if (banner) return banner;
        banner = document.createElement('div');
        banner.id = 'notesFileReferenceIssue';
        banner.className = 'notes-reference-issue';
        banner.setAttribute('role', 'alert');
        banner.setAttribute('aria-live', 'assertive');
        banner.innerHTML = `
            <div class="notes-reference-issue-copy">
                <strong></strong>
                <p></p>
                <code></code>
            </div>
            <div class="notes-reference-issue-actions">
                <button type="button" data-reference-action="owner-request"></button>
                <button type="button" data-reference-action="replace"></button>
                <button type="button" data-reference-action="remove"></button>
            </div>
        `;
        editorShell.before(banner);
        banner.querySelector('[data-reference-action="owner-request"]')?.addEventListener('click', () => this.copyFileOwnerRequest());
        banner.querySelector('[data-reference-action="replace"]')?.addEventListener('click', () => this.openFilePicker('replace'));
        banner.querySelector('[data-reference-action="remove"]')?.addEventListener('click', () => this.removeUnavailableReference());
        return banner;
    },

    showFileReferenceIssue(error) {
        const issue = this.getFileReferenceIssueFromError(error);
        if (!issue) return false;
        NotesState.referenceIssue = issue;
        const banner = this.ensureFileReferenceIssueBanner();
        if (!banner) return false;
        const displayName = issue.label || issue.file_id;
        const title = banner.querySelector('strong');
        const description = banner.querySelector('p');
        const marker = banner.querySelector('code');
        if (title) title.textContent = notesT('notes_reference_unavailable_title', 'File reference needs attention');
        if (description) {
            description.textContent = notesFormatT(
                'notes_reference_unavailable_message',
                'You cannot add “{name}” because you do not have access. Ask the file owner to share its containing folder, or replace or remove this reference.',
                { name: displayName },
            );
        }
        if (marker) {
            marker.textContent = notesFormatT(
                'notes_reference_exact_marker',
                'Blocking reference: {marker}',
                { marker: issue.raw_token },
            );
        }
        const requestButton = banner.querySelector('[data-reference-action="owner-request"]');
        const replaceButton = banner.querySelector('[data-reference-action="replace"]');
        const removeButton = banner.querySelector('[data-reference-action="remove"]');
        if (requestButton) requestButton.textContent = notesT('notes_reference_copy_owner_request', 'Ask owner to share');
        if (replaceButton) replaceButton.textContent = notesT('notes_reference_replace_action', 'Replace file');
        if (removeButton) removeButton.textContent = notesT('notes_reference_remove_action', 'Remove reference');
        banner.hidden = false;
        requestAnimationFrame(() => banner.scrollIntoView?.({ block: 'nearest', behavior: 'auto' }));
        return true;
    },

    hideFileReferenceIssue() {
        const banner = document.getElementById('notesFileReferenceIssue');
        if (banner) banner.hidden = true;
        NotesState.referenceIssue = null;
    },

    async copyFileOwnerRequest() {
        const issue = NotesState.referenceIssue;
        if (!issue) return;
        const displayName = issue.label || issue.file_id;
        const requestText = notesFormatT(
            'notes_reference_owner_request_text',
            'Please share the containing Workspace Files folder for “{name}” (file {fileId}) with me, or replace or remove it from the shared note.',
            { name: displayName, fileId: issue.file_id },
        );
        try {
            if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                await navigator.clipboard.writeText(requestText);
            } else {
                const textarea = document.createElement('textarea');
                textarea.value = requestText;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                const copied = document.execCommand('copy');
                textarea.remove();
                if (!copied) throw new Error('Clipboard copy failed');
            }
            showNotification?.(notesT('notes_reference_owner_request_copied', 'Request for the file owner copied'), 'success');
        } catch (error) {
            console.error('Failed to copy note file owner request:', error);
            showNotification?.(notesT('notes_reference_owner_request_copy_failed', 'Could not copy the owner request'), 'error');
        }
    },

    removeUnavailableReference() {
        const issue = NotesState.referenceIssue;
        if (!issue || !this.ensureEditableForEmbeddedFiles()) return false;
        const content = this.getCurrentEditorContent();
        const nextContent = replaceFirstNoteReferenceToken(content, issue.raw_token);
        if (nextContent === null) {
            this.hideFileReferenceIssue();
            return false;
        }
        this.hideFileReferenceIssue();
        this.setCurrentEditorContent(nextContent, { editable: true, focus: true });
        this.handleEditorInput(nextContent);
        showNotification?.(notesT('notes_reference_removed', 'Unavailable reference removed'), 'success');
        return true;
    },

    replaceUnavailableReferenceWithFile(file) {
        const issue = NotesState.referenceIssue;
        if (!issue || !file || !this.ensureEditableForEmbeddedFiles()) return false;
        const content = this.getCurrentEditorContent();
        if (!content.includes(issue.raw_token)) {
            this.hideFileReferenceIssue();
            return false;
        }
        const replacement = NotesUtils.buildAccessibleFileReference({
            kind: ['image', 'audio', 'file'].includes(issue.kind) ? issue.kind : 'file',
            file,
        });
        if (!replacement) return false;
        const nextContent = replaceFirstNoteReferenceToken(content, issue.raw_token, replacement);
        if (nextContent === null) return false;
        this.upsertReferencedFiles([file], issue.kind);
        this.hideFileReferenceIssue();
        this.setCurrentEditorContent(nextContent, { editable: true, focus: true });
        this.handleEditorInput(nextContent);
        showNotification?.(notesT('notes_reference_replaced', 'File reference replaced'), 'success');
        return true;
    },

    async saveCurrentNote(noteId = NotesState.selectedNoteId) {
        if (!noteId || NotesState.isSaving) return false;

        const content = this.getCurrentEditorContent();
        const expectedUpdatedAt = NotesState.selectedNoteId === noteId
            ? NotesState.currentNoteUpdatedAt
            : '';
        this.syncEditorMirror(content);

        // Don't save if content hasn't changed
        if (content === NotesState.lastSavedContent) {
            NotesState.hasUnsavedChanges = false;
            this.updateSaveStatus('saved');
            return true;
        }

        NotesState.isSaving = true;

        try {
            const updatedNote = await NotesAPI.updateNote(noteId, content, expectedUpdatedAt);
            
            // Update note in state (keep list format, just update title/snippet/date)
            const noteIndex = NotesState.notes.findIndex(n => n.id === noteId);
            if (noteIndex !== -1) {
                NotesState.notes[noteIndex] = {
                    ...NotesState.notes[noteIndex],
                    title: this.extractTitle(content),
                    snippet: this.extractSnippet(content),
                    updated_at: updatedNote.updated_at,
                };
                this.sortNotesState();
            }

            if (NotesState.selectedNoteId === noteId) {
                NotesState.lastSavedContent = content;
                NotesState.currentNoteContent = content;
                NotesState.currentNoteUpdatedAt = normalizeNoteRevisionToken(updatedNote.updated_at);
                NotesState.hasUnsavedChanges = false;
                NotesState.remoteUpdate = null;
                this.hideRemoteUpdateBanner();
                this.updateSaveStatus('saved');
                this.hideFileReferenceIssue();
            }
            void window.NotesConflictManager?.deleteRecovery?.(noteId);

            // Update sidebar
            this.renderCurrentNotesList();
            return true;
        } catch (error) {
            console.error('Failed to save note:', error);
            if (isNoteRevisionConflict(error)) {
                await this.openConflictRecovery({
                    noteId,
                    baseContent: NotesState.lastSavedContent,
                    localContent: content,
                    baseRevision: expectedUpdatedAt,
                });
                return false;
            }
            if (isNoteFileReferenceUnavailable(error) && this.showFileReferenceIssue(error)) {
                this.updateSaveStatus('error');
                return false;
            }
            this.updateSaveStatus('error');
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_error_save_note', 'Failed to save note'), 'error');
            }
            return false;
        } finally {
            NotesState.isSaving = false;
        }
    },

    extractTitle(content, maxLength = 50) {
        const plainText = NotesUtils.toPlainText(content);
        if (!plainText) return notesT('notes_accept_untitled', 'Untitled note');
        const firstLine = plainText.split('\n')[0].trim();
        if (!firstLine) return notesT('notes_accept_untitled', 'Untitled note');
        if (firstLine.length <= maxLength) return firstLine;
        return firstLine.substring(0, maxLength) + '…';
    },

    extractSnippet(content, maxLength = 120) {
        const plainText = NotesUtils.toPlainText(content);
        if (!plainText) return '';
        const lines = plainText.split('\n');
        if (lines.length <= 1) return '';
        const rest = lines.slice(1).map(l => l.trim()).filter(Boolean).join(' ');
        if (!rest) return '';
        if (rest.length <= maxLength) return rest;
        return rest.substring(0, maxLength) + '…';
    },

    updateSaveStatus(status) {
        const saveStatus = NotesDOM.saveStatus;
        if (!saveStatus) return;

        saveStatus.className = 'notes-save-status';
        
        switch (status) {
            case 'loading':
                saveStatus.innerHTML = `
                    <div class="notes-save-status-indicator loading"></div>
                    <span>${notesT('notes_status_loading', 'Loading...')}</span>
                `;
                saveStatus.classList.add('loading');
                break;
            case 'saving':
                saveStatus.innerHTML = `
                    <div class="notes-save-status-indicator saving"></div>
                    <span>${notesT('notes_status_saving', 'Saving changes...')}</span>
                `;
                saveStatus.classList.add('saving');
                break;
            case 'saved':
                saveStatus.innerHTML = `
                    <div class="notes-save-status-indicator saved"></div>
                    <span>${notesT('notes_status_saved', 'Saved')}</span>
                `;
                saveStatus.classList.add('saved');
                break;
            case 'error':
                saveStatus.innerHTML = `
                    <div class="notes-save-status-indicator error"></div>
                    <span>${notesT('notes_status_error', 'Error saving')}</span>
                `;
                saveStatus.classList.add('error');
                break;
            case 'conflict':
                saveStatus.innerHTML = `
                    <div class="notes-save-status-indicator conflict"></div>
                    <span>${notesT('notes_status_conflict', 'Conflict needs review')}</span>
                `;
                saveStatus.classList.add('conflict');
                break;
            case 'readonly':
                saveStatus.innerHTML = `
                    <div class="notes-save-status-indicator readonly"></div>
                    <span>${notesT('notes_shared_view_only', 'View only')}</span>
                `;
                saveStatus.classList.add('readonly');
                break;
        }
    },

    showRemoteUpdateBanner(snapshot = null, { conflict = false } = {}) {
        if (snapshot) NotesState.remoteUpdate = snapshot;
        let banner = document.getElementById('notesRemoteUpdateBanner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'notesRemoteUpdateBanner';
            banner.className = 'notes-remote-update-banner';
            banner.innerHTML = `<span></span><button type="button"></button>`;
            NotesDOM.editorHeaderLeading?.appendChild(banner);
            banner.querySelector('button')?.addEventListener('click', () => this.reviewRemoteUpdate());
        }
        banner.querySelector('span').textContent = conflict
            ? notesT('notes_conflict_draft_safe', 'Your draft is safe and needs review.')
            : notesT('notes_remote_update_available', 'A newer version is available.');
        banner.querySelector('button').textContent = conflict
            ? notesT('notes_conflict_resolve', 'Resolve conflict')
            : notesT('notes_remote_update_review', 'Review update');
        banner.hidden = false;
    },

    hideRemoteUpdateBanner() {
        const banner = document.getElementById('notesRemoteUpdateBanner');
        if (banner) banner.hidden = true;
        NotesState.remoteUpdate = null;
    },

    async reviewRemoteUpdate() {
        const noteId = NotesState.selectedNoteId;
        if (!noteId) return;
        if (window.NotesConflictManager?.reopen?.(noteId)) return;
        const snapshot = NotesState.remoteUpdate;
        if (!snapshot) return;
        if (NotesState.hasUnsavedChanges) {
            await this.openConflictRecovery({ noteId, serverSnapshot: snapshot });
            return;
        }
        this.applyResolvedContent(noteId, snapshot.content || '', snapshot);
    },

    showDeleteNoteWarning(noteId) {
        NotesState.noteToDelete = noteId;
        const overlay = NotesDOM.deleteOverlay;
        if (overlay) {
            overlay.removeAttribute('hidden');
            overlay.setAttribute('aria-hidden', 'false');
        }
    },

    hideDeleteOverlay() {
        const overlay = NotesDOM.deleteOverlay;
        if (overlay) {
            overlay.setAttribute('hidden', '');
            overlay.setAttribute('aria-hidden', 'true');
        }
        NotesState.noteToDelete = null;
    },

    async confirmDeleteNote() {
        const noteId = NotesState.noteToDelete;
        if (!noteId) return;

        try {
            if (String(NotesState.selectedNoteId) === String(noteId)) {
                const saved = await this.ensureCurrentNoteSaved();
                if (!saved) return;
            }
            const note = NotesState.notes.find((item) => String(item.id) === String(noteId));
            const revision = String(NotesState.selectedNoteId) === String(noteId)
                ? NotesState.currentNoteUpdatedAt
                : note?.updated_at;
            await NotesAPI.deleteNote(noteId, revision);
            if (window.canvasFilesDropdown?.unregisterFile) {
                window.canvasFilesDropdown.unregisterFile(`note:${noteId}`);
            }
            
            // Remove from state
            NotesState.notes = NotesState.notes.filter(n => n.id !== noteId);
            
            // If deleted note was selected, clear selection
            if (NotesState.selectedNoteId === noteId) {
                NotesState.selectedNoteId = null;
                NotesState.lastSavedContent = '';
                NotesState.currentNoteUpdatedAt = '';
                NotesState.hasUnsavedChanges = false;
                
                // Show empty state or select another note
                if (NotesState.notes.length > 0) {
                    this.selectNote(NotesState.notes[0].id);
                } else {
                    this.showNoNotesState();
                }
            }

            this.renderCurrentNotesList();
            this.hideDeleteOverlay();

            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_deleted_success', 'Note deleted'), 'success');
            }
        } catch (error) {
            console.error('Failed to delete note:', error);
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_error_delete_note', 'Failed to delete note'), 'error');
            }
        }
    },

    // ============================================================================
    // Sharing Methods
    // ============================================================================

    async showShareModal(noteId) {
        const note = NotesState.notes.find(n => n.id === noteId);
        if (!note) return;
        if (!canManageNoteSharing(note)) return;

        let overlay = document.getElementById('notesShareOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'notesShareOverlay';
            overlay.className = 'cs-overlay shared-modal-overlay';
            document.body.appendChild(overlay);
        }

        NotesState.sharingNoteId = noteId;
        NotesState.shareMode = 'list';
        NotesState.shareAction = 'link';
        NotesState.shareStatus = null;
        NotesState.currentShareType = 'live';
        NotesState.selectedUserIds = [];

        this.renderShareModal(note);
        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('cs-active'));
        await this.loadShareStatus(noteId);
        const updatedNote = NotesState.notes.find(n => n.id === noteId);
        if (!updatedNote || !canManageNoteSharing(updatedNote)) {
            this.hideShareModal();
        }
    },

    getShareAction() {
        return String(document.querySelector('input[name="notesShareAction"]:checked')?.value || NotesState.shareAction || 'link');
    },

    getShareTypeSelection() {
        return String(document.querySelector('input[name="notesShareType"]:checked')?.value || NotesState.currentShareType || 'live');
    },

    getShareTypeLabel(shareType) {
        if (shareType === 'clone') return notesT('notes_share_type_clone_label', 'Clone');
        if (shareType === 'collaborate') return notesT('notes_share_type_collaborate_label', 'Collaborate');
        return notesT('notes_share_type_live_label', 'Live');
    },

    getShareTypeDescription(shareType) {
        if (shareType === 'clone') {
            return notesT('notes_share_type_clone_desc', 'Recipients get their own independent copy.');
        }
        if (shareType === 'collaborate') {
            return notesT('notes_share_type_collaborate_desc', 'Recipients can work with a synced shared note.');
        }
        return notesT('notes_share_type_live_desc', 'Recipients can view this note with live updates.');
    },

    renderShareModal(note) {
        const overlay = document.getElementById('notesShareOverlay');
        if (!overlay || !note) return;

        const status = NotesState.shareStatus || {};
        const hasShares = Boolean(status.clone_share_id || status.live_share_id || status.collaborate_share_id);
        const isListMode = NotesState.shareMode === 'list';
        const isInvite = NotesState.shareAction === 'invite';
        const title = NotesRender.getNoteTitle(note.title || note.snippet || '');
        const shares = [];
        if (status.clone_share_id) shares.push({ type: 'clone', id: status.clone_share_id, count: 0 });
        if (status.live_share_id) shares.push({ type: 'live', id: status.live_share_id, count: status.live_subscriber_count || 0 });
        if (status.collaborate_share_id) shares.push({ type: 'collaborate', id: status.collaborate_share_id, count: status.collaborate_subscriber_count || 0 });

        overlay.innerHTML = `
            <div class="cs-modal shared-modal shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="notesShareTitle" tabindex="-1">
                <header class="cs-header shared-modal-header shared-modal-header--main">
                    <div class="cs-header-text shared-modal-heading">
                        <h3 class="cs-title shared-modal-title" id="notesShareTitle">${NotesRender.escapeHtml(notesT('notes_share_title', 'Share note'))}</h3>
                        <p class="cs-subtitle shared-modal-subtitle">${NotesRender.escapeHtml(title)}</p>
                    </div>
                    <button type="button" class="cs-icon-btn shared-modal-close" id="notesShareCloseBtn" aria-label="${NotesRender.escapeHtml(notesT('notes_share_close_aria', 'Close share dialog'))}">
                        ${Icons.close}
                    </button>
                </header>
                <div class="cs-body shared-modal-body">
                    <section class="cs-section" ${isListMode && hasShares ? '' : 'hidden'}>
                        <div class="cs-section-head"><span class="cs-section-label">${NotesRender.escapeHtml(notesT('notes_share_active_links', 'Active links'))}</span></div>
                        <div class="cs-link-list" id="notesShareLinkList">${shares.map((share) => this.renderShareLinkCard(share)).join('')}</div>
                    </section>
                    <section class="cs-empty" ${isListMode && !hasShares ? '' : 'hidden'}>
                        <div class="cs-empty-icon" aria-hidden="true">${Icons.urlLink}</div>
                        <p class="cs-empty-title">${NotesRender.escapeHtml(notesT('notes_share_empty_title', 'No share link yet'))}</p>
                        <p class="cs-empty-desc">${NotesRender.escapeHtml(notesT('notes_share_empty_desc', 'Create one or more links to share this note.'))}</p>
                    </section>
                    <section class="cs-form" ${isListMode ? 'hidden' : ''}>
                        <div class="cs-section-head"><span class="cs-section-label">${NotesRender.escapeHtml(isInvite ? notesT('notes_share_invite_users', 'Invite users') : notesT('notes_share_create_new_link', 'Create new link'))}</span></div>
                        <div class="cs-field">
                            <label class="cs-field-label">${NotesRender.escapeHtml(notesT('notes_share_kind_label', 'Share kind'))}</label>
                            <div class="cs-radio-group" role="radiogroup" aria-label="${NotesRender.escapeHtml(notesT('notes_share_type_aria', 'Note share type'))}">
                                ${['live', 'collaborate', 'clone'].map((shareType) => `
                                    <label class="cs-radio">
                                        <input type="radio" name="notesShareType" value="${NotesRender.escapeHtml(shareType)}" ${NotesState.currentShareType === shareType ? 'checked' : ''}>
                                        <div class="cs-radio-content">
                                            <span class="cs-radio-title">${NotesRender.escapeHtml(this.getShareTypeLabel(shareType))}</span>
                                            <span class="cs-radio-desc">${NotesRender.escapeHtml(this.getShareTypeDescription(shareType))}</span>
                                        </div>
                                    </label>
                                `).join('')}
                            </div>
                        </div>
                        <div class="cs-field">
                            <label class="cs-field-label">${NotesRender.escapeHtml(notesT('notes_share_delivery_label', 'Delivery'))}</label>
                            <div class="cs-radio-group" role="radiogroup" aria-label="${NotesRender.escapeHtml(notesT('notes_share_delivery_aria', 'Note share delivery'))}">
                                <label class="cs-radio">
                                    <input type="radio" name="notesShareAction" value="link" ${NotesState.shareAction === 'link' ? 'checked' : ''}>
                                    <div class="cs-radio-content">
                                        <span class="cs-radio-title">${NotesRender.escapeHtml(notesT('notes_share_action_link_title', 'Create a share link'))}</span>
                                        <span class="cs-radio-desc">${NotesRender.escapeHtml(notesT('notes_share_action_link_desc', 'Generate a reusable link for the selected share kind.'))}</span>
                                    </div>
                                </label>
                                <label class="cs-radio">
                                    <input type="radio" name="notesShareAction" value="invite" ${NotesState.shareAction === 'invite' ? 'checked' : ''}>
                                    <div class="cs-radio-content">
                                        <span class="cs-radio-title">${NotesRender.escapeHtml(notesT('notes_share_action_invite_title', 'Invite specific users'))}</span>
                                        <span class="cs-radio-desc">${NotesRender.escapeHtml(notesT('notes_share_action_invite_desc', 'Send a workspace invitation using the selected share kind.'))}</span>
                                    </div>
                                </label>
                            </div>
                        </div>
                        <div class="cs-field cs-invite-field" id="notesShareInviteField" ${isInvite ? '' : 'hidden'}>
                            <label class="cs-field-label" for="notesInviteUserSearch">${NotesRender.escapeHtml(notesT('notes_share_select_users_label', 'Select users to invite'))}</label>
                            <div class="cs-invite-search">
                                ${Icons.magnifyingGlass}
                                <input type="text" id="notesInviteUserSearch" class="cs-input cs-invite-search-input" placeholder="${NotesRender.escapeHtml(notesT('notes_share_search_users_placeholder', 'Search users...'))}">
                            </div>
                            <div class="cs-invite-user-list" id="notesInviteUserList"><div class="cs-invite-state">${NotesRender.escapeHtml(NotesState.publicUsersLoaded ? notesT('notes_share_no_users_available', 'No users available to invite.') : notesT('notes_share_loading_users', 'Loading users...'))}</div></div>
                            <div class="cs-invite-selected" id="notesSelectedUsers" hidden>
                                <div class="cs-invite-selected-head">${NotesRender.escapeHtml(notesT('notes_share_selected_label', 'Selected'))} (<span id="notesSelectedCount">0</span>)</div>
                                <div class="cs-invite-selected-list" id="notesSelectedUsersList"></div>
                            </div>
                        </div>
                    </section>
                </div>
                <footer class="cs-footer shared-modal-footer">
                    <button type="button" class="cs-btn cs-btn-ghost om-button border cancel" id="notesShareSecondaryBtn">${NotesRender.escapeHtml(isListMode ? notesT('notes_share_done', 'Done') : (hasShares ? notesT('notes_share_cancel', 'Cancel') : notesT('notes_share_done', 'Done')))}</button>
                    <button type="button" class="cs-btn cs-btn-primary om-button border submit" id="notesSharePrimaryBtn">${NotesRender.escapeHtml(isListMode ? (hasShares ? notesT('notes_share_new_link', 'New link') : notesT('notes_share_create_link', 'Create link')) : (isInvite ? notesT('notes_share_send_invites', 'Send invites') : notesT('notes_share_create_link', 'Create link')))}</button>
                </footer>
            </div>
        `;

        overlay.onclick = (event) => { if (event.target === overlay) this.hideShareModal(); };
        document.getElementById('notesShareCloseBtn')?.addEventListener('click', () => this.hideShareModal());
        document.getElementById('notesShareSecondaryBtn')?.addEventListener('click', () => {
            if (NotesState.shareMode === 'list' || !hasShares) {
                this.hideShareModal();
                return;
            }
            NotesState.shareMode = 'list';
            NotesState.shareAction = 'link';
            this.renderShareModal(note);
        });
        document.getElementById('notesSharePrimaryBtn')?.addEventListener('click', async () => {
            if (NotesState.shareMode === 'list') {
                NotesState.shareMode = 'create';
                NotesState.shareAction = 'link';
                this.renderShareModal(note);
                return;
            }
            if (this.getShareAction() === 'invite') {
                await this.sendInvitations();
                return;
            }
            await this.generateShareLink();
        });
        overlay.querySelectorAll('input[name="notesShareType"]').forEach((input) => {
            input.addEventListener('change', () => {
                NotesState.currentShareType = input.value;
                this.renderShareModal(note);
            });
        });
        overlay.querySelectorAll('input[name="notesShareAction"]').forEach((input) => {
            input.addEventListener('change', () => {
                NotesState.shareAction = input.value;
                this.renderShareModal(note);
            });
        });
        this.bindShareLinkActions(note);
        const inviteSearch = document.getElementById('notesInviteUserSearch');
        inviteSearch?.addEventListener('input', (event) => this.filterInviteUsers(event.target.value));
        if (NotesState.shareAction === 'invite') {
            if (NotesState.publicUsersLoaded) {
                this.filterInviteUsers(inviteSearch?.value || '');
            } else {
                void this.loadPublicUsers();
            }
        }
    },

    renderShareLinkCard(share) {
        const shareUrl = `${window.location.origin}/notes/${share.type}/${share.id}`;
        const subscriberCount = share.count === 1
            ? notesFormatT('notes_share_subscriber_count_one', '{count} subscriber', { count: share.count })
            : notesFormatT('notes_share_subscriber_count_other', '{count} subscribers', { count: share.count });
        const subscriberChip = share.count ? `<span class="cs-chip cs-chip-muted">${NotesRender.escapeHtml(subscriberCount)}</span>` : '';
        return `
            <div class="cs-link-card" data-share-type="${NotesRender.escapeHtml(share.type)}" data-share-url="${NotesRender.escapeHtml(shareUrl)}">
                <div class="cs-link-url-row"><input type="text" class="cs-link-url" value="${NotesRender.escapeHtml(shareUrl)}" readonly aria-label="${NotesRender.escapeHtml(notesT('notes_share_link_aria', 'Note share link'))}"></div>
                <div class="cs-link-meta"><span class="cs-chip">${NotesRender.escapeHtml(this.getShareTypeLabel(share.type))}</span>${subscriberChip}</div>
                <div class="cs-link-actions">
                    <button type="button" class="om-button border cancel" data-action="copy">${Icons.copy}${NotesRender.escapeHtml(notesT('notes_share_copy_action', 'Copy'))}</button>
                    <button type="button" class="om-button border cancel" data-action="open">${Icons.open_window}${NotesRender.escapeHtml(notesT('notes_share_open_action', 'Open'))}</button>
                    <button type="button" class="om-button border cancel" data-action="edit">${Icons.create}${NotesRender.escapeHtml(notesT('notes_share_edit_action', 'Edit'))}</button>
                    <button type="button" class="om-button border danger-nofill" data-action="delete">${Icons.trash}${NotesRender.escapeHtml(notesT('notes_share_delete_action', 'Delete'))}</button>
                </div>
            </div>
        `;
    },

    bindShareLinkActions(note) {
        const overlay = document.getElementById('notesShareOverlay');
        if (!overlay) return;
        overlay.querySelectorAll('.cs-link-card').forEach((card) => {
            const shareType = card.dataset.shareType;
            const shareUrl = card.dataset.shareUrl;
            card.querySelector('[data-action="copy"]')?.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(shareUrl);
                    if (typeof showNotification === 'function') showNotification(notesT('notes_share_copied', 'Copied!'), 'success');
                } catch (error) {
                    console.error('Copy failed:', error);
                }
            });
            card.querySelector('[data-action="open"]')?.addEventListener('click', () => {
                if (shareUrl) window.open(shareUrl, '_blank', 'noopener,noreferrer');
            });
            card.querySelector('[data-action="edit"]')?.addEventListener('click', () => {
                NotesState.shareMode = 'create';
                NotesState.shareAction = 'link';
                NotesState.currentShareType = shareType;
                this.renderShareModal(note);
            });
            card.querySelector('[data-action="delete"]')?.addEventListener('click', async () => {
                await this.stopSharingByType(shareType);
            });
        });
    },

    async loadPublicUsers() {
        const userList = document.getElementById('notesInviteUserList');
        if (!userList || NotesState.publicUsersLoading || NotesState.publicUsersLoaded) return;
        NotesState.publicUsersLoading = true;
        userList.innerHTML = `<div class="cs-invite-state">${NotesRender.escapeHtml(notesT('notes_share_loading_users', 'Loading users...'))}</div>`;
        try {
            NotesState.publicUsers = await NotesAPI.fetchPublicUsers();
            NotesState.publicUsersLoaded = true;
            this.filterInviteUsers('');
        } catch (error) {
            console.error('Failed to load public users:', error);
            userList.innerHTML = `<div class="cs-invite-state">${NotesRender.escapeHtml(notesT('notes_share_load_users_failed', 'Failed to load users.'))}</div>`;
        } finally {
            NotesState.publicUsersLoading = false;
        }
    },

    renderInviteUserList(users) {
        const userList = document.getElementById('notesInviteUserList');
        if (!userList) return;
        if (!users || users.length === 0) {
            userList.innerHTML = `<div class="cs-invite-state">${NotesRender.escapeHtml(notesT('notes_share_no_users_available', 'No users available to invite.'))}</div>`;
            return;
        }
        userList.innerHTML = users.map((user) => {
            const isSelected = NotesState.selectedUserIds.includes(user.id);
            const initials = this.getUserInitials(user);
            return `
                <button type="button" class="cs-invite-user-item ${isSelected ? 'is-selected' : ''}" data-user-id="${NotesRender.escapeHtml(user.id)}">
                    <span class="cs-invite-avatar">${NotesRender.escapeHtml(initials)}</span>
                    <span class="cs-invite-user-info">
                        <span class="cs-invite-user-name">${NotesRender.escapeHtml(user.display_name)}</span>
                    </span>
                    <span class="cs-invite-check" aria-hidden="true">${Icons.check}</span>
                </button>
            `;
        }).join('');
        userList.querySelectorAll('.cs-invite-user-item').forEach((item) => {
            item.addEventListener('click', () => this.toggleUserSelection(item.dataset.userId));
        });
    },

    getUserInitials(user) {
        if (user.first_name && user.last_name) return (user.first_name[0] + user.last_name[0]).toUpperCase();
        if (user.first_name) return user.first_name.substring(0, 2).toUpperCase();
        if (user.display_name) return user.display_name.substring(0, 2).toUpperCase();
        return '??';
    },

    toggleUserSelection(userId) {
        const idx = NotesState.selectedUserIds.indexOf(userId);
        if (idx >= 0) NotesState.selectedUserIds.splice(idx, 1);
        else NotesState.selectedUserIds.push(userId);
        this.updateSelectedUsersUI();
    },

    updateSelectedUsersUI() {
        const selectedSection = document.getElementById('notesSelectedUsers');
        const selectedList = document.getElementById('notesSelectedUsersList');
        const selectedCount = document.getElementById('notesSelectedCount');
        document.querySelectorAll('#notesInviteUserList .cs-invite-user-item').forEach((item) => {
            item.classList.toggle('is-selected', NotesState.selectedUserIds.includes(item.dataset.userId));
        });
        if (!NotesState.selectedUserIds.length) {
            if (selectedSection) selectedSection.hidden = true;
            if (selectedList) selectedList.innerHTML = '';
            if (selectedCount) selectedCount.textContent = '0';
            return;
        }
        if (selectedSection) selectedSection.hidden = false;
        if (selectedCount) selectedCount.textContent = String(NotesState.selectedUserIds.length);
        const selectedUsers = NotesState.publicUsers.filter((user) => NotesState.selectedUserIds.includes(user.id));
        if (selectedList) {
            selectedList.innerHTML = selectedUsers.map((user) => `
                <span class="cs-invite-selected-chip">
                    <span>${NotesRender.escapeHtml(user.display_name)}</span>
                    <button type="button" data-user-id="${NotesRender.escapeHtml(user.id)}" aria-label="${NotesRender.escapeHtml(notesT('notes_share_remove_user_aria', 'Remove user'))}">${Icons.close}</button>
                </span>
            `).join('');
            selectedList.querySelectorAll('button[data-user-id]').forEach((btn) => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    this.toggleUserSelection(btn.dataset.userId);
                });
            });
        }
    },

    filterInviteUsers(searchTerm) {
        const term = String(searchTerm || '').toLowerCase().trim();
        const filtered = term
            ? NotesState.publicUsers.filter((user) =>
                (user.display_name && user.display_name.toLowerCase().includes(term)) ||
                false)
            : NotesState.publicUsers;
        this.renderInviteUserList(filtered);
        this.updateSelectedUsersUI();
    },

    async sendInvitations() {
        const noteId = NotesState.sharingNoteId;
        if (!noteId || NotesState.selectedUserIds.length === 0) return;
        const btn = document.getElementById('notesSharePrimaryBtn');
        if (btn) btn.disabled = true;
        try {
            const result = await NotesAPI.inviteUsersToNote(noteId, NotesState.selectedUserIds, this.getShareTypeSelection());
            if (typeof showNotification === 'function') {
                showNotification(result.message || notesFormatT('notes_share_invited_fallback', 'Invited {count} user(s)', { count: result.invited_count }), 'success');
            }
            NotesState.selectedUserIds = [];
            NotesState.shareMode = 'list';
            const note = NotesState.notes.find((n) => n.id === noteId);
            if (!note) {
                if (typeof showNotification === 'function') showNotification(notesT('notes_share_note_not_found', 'Note not found'), 'error');
                return;
            }
            this.renderShareModal(note);
        } catch (error) {
            console.error('Failed to send invitations:', error);
            if (typeof showNotification === 'function') showNotification(notesT('notes_share_invite_failed', 'Failed to send invitations'), 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    async loadShareStatus(noteId) {
        try {
            NotesState.shareStatus = await NotesAPI.getShareStatus(noteId);
            const idx = NotesState.notes.findIndex((n) => n.id === noteId);
            if (idx >= 0) {
                NotesState.notes[idx].clone_share_id = NotesState.shareStatus.clone_share_id;
                NotesState.notes[idx].live_share_id = NotesState.shareStatus.live_share_id;
                NotesState.notes[idx].collaborate_share_id = NotesState.shareStatus.collaborate_share_id;
                NotesState.notes[idx].subscriber_count = NotesState.shareStatus.subscriber_count;
            }
            const note = NotesState.notes.find((n) => n.id === noteId);
            if (note) {
                this.renderShareModal(note);
            }
        } catch (error) {
            console.error('Failed to load share status:', error);
        }
    },

    async generateShareLink() {
        const noteId = NotesState.sharingNoteId;
        if (!noteId) return;
        const btn = document.getElementById('notesSharePrimaryBtn');
        if (btn) btn.disabled = true;
        try {
            const shareData = await NotesAPI.shareNote(noteId, this.getShareTypeSelection());
            const shareUrl = (typeof shareData.share_url === 'string' && /^https?:\/\//i.test(shareData.share_url))
                ? shareData.share_url
                : `${window.location.origin}${shareData.share_url || ''}`;
            try {
                await navigator.clipboard.writeText(shareUrl);
            } catch (_) {
                // ignore clipboard failure
            }
            await this.loadShareStatus(noteId);
            this.renderCurrentNotesList();
            NotesState.shareMode = 'list';
            const note = NotesState.notes.find((n) => n.id === noteId);
            if (!note) {
                if (typeof showNotification === 'function') showNotification(notesT('notes_share_note_not_found', 'Note not found'), 'error');
                return;
            }
            this.renderShareModal(note);
        } catch (error) {
            console.error('Failed to generate share link:', error);
            if (typeof showNotification === 'function') showNotification(notesT('notes_share_generate_failed', 'Failed to generate share link'), 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    hideShareModal() {
        const overlay = document.getElementById('notesShareOverlay');
        if (overlay) {
            overlay.classList.remove('cs-active');
            setTimeout(() => overlay.setAttribute('hidden', ''), 200);
        }
        NotesState.sharingNoteId = null;
        NotesState.shareMode = 'list';
    },

    async stopSharingByType(shareType) {
        const noteId = NotesState.sharingNoteId;
        if (!noteId) return;

        try {
            await NotesAPI.deleteShare(noteId, shareType);
            await this.loadShareStatus(noteId);
            this.renderCurrentNotesList();
            
            if (typeof showNotification === 'function') {
                showNotification(notesFormatT('notes_share_stopped', '{type} sharing stopped', {
                    type: this.getShareTypeLabel(shareType),
                }), 'success');
            }
        } catch (error) {
            console.error('Failed to stop sharing:', error);
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_share_stop_failed', 'Failed to stop sharing'), 'error');
            }
        }
    },

    async stopSharing() {
        const noteId = NotesState.sharingNoteId;
        if (!noteId) return;

        try {
            await NotesAPI.deleteShare(noteId);
            
            // Update note in state
            const idx = NotesState.notes.findIndex(n => n.id === noteId);
            if (idx >= 0) {
                NotesState.notes[idx].share_id = null;
                NotesState.notes[idx].clone_share_id = null;
                NotesState.notes[idx].live_share_id = null;
                NotesState.notes[idx].collaborate_share_id = null;
                NotesState.notes[idx].subscriber_count = null;
            }
            
            this.renderCurrentNotesList();
            this.hideShareModal();
            
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_share_all_stopped', 'All sharing stopped'), 'success');
            }
        } catch (error) {
            console.error('Failed to stop sharing:', error);
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_share_stop_failed', 'Failed to stop sharing'), 'error');
            }
        }
    },

    async handleUnsubscribe(noteId) {
        const note = NotesState.notes.find(n => n.id === noteId);
        if (!note) return;

        const title = NotesRender.getNoteTitle(note.content);
        if (!await window.showDeleteConfirm({
            title: notesT('common_remove_confirm_title', 'Remove item?'),
            message: notesFormatT('notes_unsubscribe_confirm', 'Remove "{title}" from your workspace? You can add it back later using the share link.', { title }),
            confirmLabel: notesT('notes_remove_from_workspace', 'Remove from workspace'),
        })) {
            return;
        }

        try {
            await NotesAPI.unsubscribeFromNote(noteId);
            
            // Remove from state
            NotesState.notes = NotesState.notes.filter(n => n.id !== noteId);
            
            // If this was the selected note, clear selection
            if (NotesState.selectedNoteId === noteId) {
                NotesState.selectedNoteId = null;
                NotesState.lastSavedContent = '';
                NotesState.currentNoteUpdatedAt = '';
                NotesState.hasUnsavedChanges = false;
                if (NotesState.notes.length > 0) {
                    this.selectNote(NotesState.notes[0].id);
                } else {
                    this.showNoNotesState();
                }
            }
            
            this.renderCurrentNotesList();
            
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_unsubscribe_success', 'Note removed from workspace'), 'success');
            }
        } catch (error) {
            console.error('Failed to unsubscribe:', error);
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_unsubscribe_remove_failed', 'Failed to remove note'), 'error');
            }
        }
    },

    // Called when switching to Notes tab
    async show() {
        await this.init();
        await this.loadNotes();
    },

    // Called when switching away from Notes tab
    hide() {
        this.stopAutoRefresh();
        this.closeFilePicker();
        this.resetRecordingState();
        this.closeRecordingModal();
    },

    // ============================================================================
    // Accept Shared Note Methods
    // ============================================================================

    async showAcceptModal(shareId, shareType = null) {
        NotesState.pendingShareId = shareId;
        NotesState.pendingShareType = shareType;
        
        const overlay = document.getElementById('noteAcceptOverlay');
        const titleEl = document.getElementById('noteAcceptTitle');
        const ownerEl = document.getElementById('noteAcceptOwner');
        const previewEl = document.getElementById('noteAcceptPreviewContent');
        const confirmBtn = document.getElementById('noteAcceptConfirmBtn');
        const shareTypeInfoEl = document.getElementById('noteAcceptShareTypeInfo');
        
        if (!overlay) return;

        // Reset and show loading state
        titleEl.textContent = notesT('notes_accept_loading', 'Loading...');
        ownerEl.textContent = '';
        previewEl.innerHTML = '';
        if (shareTypeInfoEl) shareTypeInfoEl.innerHTML = '';
        confirmBtn.disabled = true;

        // Show modal
        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('active'));

        // Setup event listeners (only once)
        if (!NotesState.acceptModalInitialized) {
            document.getElementById('noteAcceptCancelBtn')?.addEventListener('click', () => this.hideAcceptModal());
            document.getElementById('noteAcceptConfirmBtn')?.addEventListener('click', () => this.confirmAcceptShared());
            overlay.addEventListener('click', (e) => { if (e.target === overlay) this.hideAcceptModal(); });
            NotesState.acceptModalInitialized = true;
        }

        // Fetch preview data
        try {
            const data = await NotesAPI.getSharedNotePreview(shareId);
            const noteTitle = NotesRender.getNoteTitle(data.content);
            titleEl.textContent = noteTitle || notesT('notes_accept_untitled', 'Untitled note');
            ownerEl.textContent = data.owner_name ? notesFormatT('notes_shared_by', 'Shared by {owner}', { owner: data.owner_name }) : '';
            
            // Store detected share type
            NotesState.pendingShareType = data.share_type || shareType;
            
            // Show share type info
            if (shareTypeInfoEl) {
                const typeLabels = {
                    'clone': { label: notesT('notes_accept_type_clone_label', 'Clone'), desc: notesT('notes_accept_type_clone_desc', 'You\'ll get your own copy that you can edit and delete freely.'), icon: 'copy', color: '#8b5cf6' },
                    'live': { label: notesT('notes_accept_type_live_label', 'Live view'), desc: notesT('notes_accept_type_live_desc', 'View-only with live updates. You cannot edit this note.'), icon: 'eye', color: '#3b82f6' },
                    'collaborate': { label: notesT('notes_accept_type_collaborate_label', 'Collaborate'), desc: notesT('notes_accept_type_collaborate_desc', 'You can view and possibly edit this note with live sync.'), icon: 'users', color: '#10b981' },
                };
                const typeInfo = typeLabels[data.share_type] || typeLabels['live'];
                shareTypeInfoEl.innerHTML = `
                    <div class="note-accept-share-type" style="background-color: ${typeInfo.color}20; border-color: ${typeInfo.color};">
                        <span class="note-accept-share-type-label" style="color: ${typeInfo.color};">${typeInfo.label}</span>
                        <span class="note-accept-share-type-desc">${typeInfo.desc}</span>
                    </div>
                `;
            }
            
            // Show preview of note content
            if (data.content) {
                const previewText = NotesUtils.toPlainText(data.content);
                const preview = previewText.substring(0, 300);
                previewEl.innerHTML = `<p>${NotesRender.escapeHtml(preview)}${previewText.length > 300 ? '...' : ''}</p>`;
            } else {
                previewEl.innerHTML = `<p style="color: var(--text-color-secondary);">${NotesRender.escapeHtml(notesT('notes_accept_empty_note', 'Empty note'))}</p>`;
            }
            
            // Update button text based on share type
            if (data.share_type === 'clone') {
                confirmBtn.innerHTML = `${Icons.copy} ${NotesRender.escapeHtml(notesT('notes_accept_clone_action', 'Clone to My Notes'))}`;
            } else {
                confirmBtn.innerHTML = `${Icons.plus} ${NotesRender.escapeHtml(notesT('notes_accept_add_action', 'Add to My Notes'))}`;
            }
            
            confirmBtn.disabled = false;
        } catch (error) {
            const isOwnerError = error && (error.status === 400);
            const isDuplicateError = error && (error.status === 409);
            if (isOwnerError) {
                console.warn('Owner attempted to open own shared note');
                this.hideAcceptModal();
                const warnMessage = error?.message || notesT('notes_accept_own_note_error', 'You cannot open your own shared note.');
                if (typeof notifyWarning === 'function') {
                    notifyWarning(warnMessage);
                } else if (typeof showNotification === 'function') {
                    showNotification(warnMessage, 'warning');
                }
                if (typeof window !== 'undefined') {
                    const path = window.location.pathname;
                    const isSharePath = /\/notes\/(clone|live|collaborate)\//.test(path);
                    if (isSharePath) {
                        history.replaceState(null, '', '/workspace/notes');
                    }
                }
                return;
            } else if (isDuplicateError) {
                console.warn('User attempted to re-add an existing shared note');
                this.hideAcceptModal();
                const errorMessage = error?.message || notesT('notes_accept_duplicate_error', 'You already added this shared note.');
                if (typeof notifyError === 'function') {
                    notifyError(errorMessage);
                } else if (typeof showNotification === 'function') {
                    showNotification(errorMessage, 'error');
                }
                if (typeof window !== 'undefined') {
                    const path = window.location.pathname;
                    const isSharePath = /\/notes\/(clone|live|collaborate)\//.test(path);
                    if (isSharePath) {
                        history.replaceState(null, '', '/workspace/notes');
                    }
                }
                return;
            }
            console.error('Failed to load shared note preview:', error);
            titleEl.textContent = notesT('notes_accept_load_error_title', 'Error loading note');
            previewEl.innerHTML = `<p style="color: #ef4444;">${NotesRender.escapeHtml(notesT('notes_accept_load_error_desc', 'Could not load this shared note. It may no longer exist.'))}</p>`;
        }
    },

    hideAcceptModal() {
        const overlay = document.getElementById('noteAcceptOverlay');
        if (overlay) {
            overlay.classList.remove('active');
            setTimeout(() => overlay.setAttribute('hidden', ''), 200);
        }
        NotesState.pendingShareId = null;
        NotesState.pendingShareType = null;
    },

    async confirmAcceptShared() {
        const shareId = NotesState.pendingShareId;
        const shareType = NotesState.pendingShareType;
        if (!shareId) return;

        const confirmBtn = document.getElementById('noteAcceptConfirmBtn');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = `${Icons.omlorix} ${NotesRender.escapeHtml(notesT('notes_accept_processing', 'Processing...'))}`;
        }

        try {
            let message = '';
            
            if (shareType === 'clone') {
                // Clone the note
                const result = await NotesAPI.cloneNote(shareId);
                message = result.message || notesT('notes_accept_clone_success', 'Note cloned successfully!');
            } else {
                // Subscribe to the note (live or collaborate)
                const result = await NotesAPI.acceptSharedNote(shareId);
                message = result.message || notesT('notes_accept_add_success', 'Note added to your workspace!');
            }
            
            this.hideAcceptModal();
            
            // Reload notes to include the new note
            await this.loadNotes();
            
            if (typeof showNotification === 'function') {
                showNotification(message, 'success');
            }
            
            // Clear URL if it was a share link
            const path = window.location.pathname;
            if (path.includes('/notes/clone/') || path.includes('/notes/live/') || path.includes('/notes/collaborate/')) {
                history.replaceState(null, '', '/workspace/notes');
            }
        } catch (error) {
            console.error('Failed to accept shared note:', error);
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_accept_add_failed', 'Failed to add note'), 'error');
            }
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.innerHTML = `${Icons.plus} ${NotesRender.escapeHtml(notesT('notes_accept_add_action', 'Add to My Notes'))}`;
            }
        }
    },

    checkForSharedLink() {
        const path = window.location.pathname;
        
        // Accept the three current share-link forms.
        const cloneMatch = path.match(/\/notes\/clone\/([a-zA-Z0-9-]+)/);
        if (cloneMatch) {
            this.ensureWorkspaceVisible();
            this.showAcceptModal(cloneMatch[1], 'clone');
            return true;
        }
        
        const liveMatch = path.match(/\/notes\/live\/([a-zA-Z0-9-]+)/);
        if (liveMatch) {
            this.ensureWorkspaceVisible();
            this.showAcceptModal(liveMatch[1], 'live');
            return true;
        }
        
        const collaborateMatch = path.match(/\/notes\/collaborate\/([a-zA-Z0-9-]+)/);
        if (collaborateMatch) {
            this.ensureWorkspaceVisible();
            this.showAcceptModal(collaborateMatch[1], 'collaborate');
            return true;
        }
        
        return false;
    },

    ensureWorkspaceVisible() {
        if (typeof showWorkspaceContainer === 'function') {
            showWorkspaceContainer({ tab: 'notes' });
            return;
        }

        if (typeof WorkspaceManager !== 'undefined') {
            WorkspaceManager.setActiveTab?.('notes');
            WorkspaceManager.show?.();
            WorkspaceManager.switchToTab?.('notes');
        }
    },

    // ============================================================================
    // Auto-Refresh for Shared Notes
    // ============================================================================

    shouldAutoRefreshNote(noteId) {
        const note = NotesState.notes.find(n => n.id === noteId);
        if (!note) return false;
        if (note.is_subscribed === true) return true;
        // Owners should auto-refresh if the note has an active live or collaborate share
        return Boolean(note.live_share_id || note.collaborate_share_id);
    },

    startAutoRefresh(noteId) {
        this.stopAutoRefresh();
        
        if (!this.shouldAutoRefreshNote(noteId)) return;
        
        // Generate initial hash based on current content
        NotesState.lastContentHash = this.generateContentHash(NotesState.currentNoteContent);
        
        NotesState.refreshInterval = setInterval(() => {
            this.refreshSharedNoteContent(noteId);
        }, NotesState.refreshIntervalMs);
    },

    stopAutoRefresh() {
        if (NotesState.refreshInterval) {
            clearInterval(NotesState.refreshInterval);
            NotesState.refreshInterval = null;
        }
        NotesState.lastContentHash = null;
        NotesState.refreshRequestToken = null;
    },

    generateContentHash(content) {
        if (!content) return '';
        // Simple hash based on content length and first/last chars
        return `${content.length}:${content.slice(0, 50)}:${content.slice(-50)}`;
    },

    isUserCurrentlyEditing() {
        // Check if the shared editor, its source textarea, or the fallback
        // textarea is focused before applying remote shared-note refreshes.
        const editorHost = NotesDOM.markdownEditorHost;
        const textarea = NotesDOM.editorTextarea;
        if (document.activeElement === textarea || editorHost?.contains(document.activeElement)) return true;
        
        // Check if there are unsaved changes (user is actively editing)
        if (NotesState.hasUnsavedChanges) return true;
        
        // Check if currently saving
        if (NotesState.isSaving) return true;
        
        // Check if loading content
        if (NotesState.isLoadingContent) return true;
        
        return false;
    },

    async refreshSharedNoteContent(noteId) {
        // Don't refresh if note changed
        if (NotesState.selectedNoteId !== noteId) {
            this.stopAutoRefresh();
            return;
        }
        
        // Saving and initial loading already own the revision transition. Keep
        // polling while focused or dirty, but never replace that editor text.
        if (NotesState.isSaving || NotesState.isLoadingContent || NotesState.refreshRequestToken) return;

        const requestToken = Symbol('shared-note-refresh');
        NotesState.refreshRequestToken = requestToken;
        
        try {
            // Fetch only the selected note's content (lightweight)
            const contentData = await NotesAPI.fetchNoteContent(noteId);
            
            // Verify we're still on the same note and this is the current poll.
            if (NotesState.selectedNoteId !== noteId || NotesState.refreshRequestToken !== requestToken) return;
            
            const newHash = this.generateContentHash(contentData.content);
            const newRevision = normalizeNoteRevisionToken(contentData.updated_at);
            const knownRevision = normalizeNoteRevisionToken(NotesState.currentNoteUpdatedAt);
            
            // Prefer the server revision, with the content hash retained for
            // older records whose timestamp may be absent.
            if ((newRevision && newRevision !== knownRevision) || (!newRevision && newHash !== NotesState.lastContentHash)) {
                if (this.isUserCurrentlyEditing() || window.NotesConflictManager?.isActiveFor?.(noteId)) {
                    NotesState.remoteUpdate = contentData;
                    window.NotesConflictManager?.updateServerSnapshot?.(noteId, contentData);
                    this.showRemoteUpdateBanner(contentData, {
                        conflict: window.NotesConflictManager?.isActiveFor?.(noteId),
                    });
                    return;
                }
                NotesState.lastContentHash = newHash;
                NotesState.currentNoteContent = contentData.content;
                NotesState.currentNoteUpdatedAt = normalizeNoteRevisionToken(contentData.updated_at);
                NotesState.referencedFiles = Array.isArray(contentData.referenced_files) ? contentData.referenced_files : [];
                
                // Update the shared editor without marking the remote refresh as
                // a local edit, then refresh the preview and sidebar metadata.
                this.setCurrentEditorContent(contentData.content || '', {
                    editable: NotesState.canEditCurrentNote,
                });
                NotesState.lastSavedContent = contentData.content || '';
                NotesState.hasUnsavedChanges = false;
                NotesState.remoteUpdate = null;
                this.hideRemoteUpdateBanner();
                this.updateSaveStatus(NotesState.canEditCurrentNote ? 'saved' : 'readonly');
                this.renderEmbeddedFilesUi(contentData.content || '');
                
                // Also refresh sidebar to update title/snippet if changed
                this.refreshSidebarNote(noteId);
            }
        } catch (error) {
            console.warn('Failed to refresh shared note:', error);
        } finally {
            if (NotesState.refreshRequestToken === requestToken) {
                NotesState.refreshRequestToken = null;
            }
        }
    },

    async refreshSidebarNote(noteId) {
        // Fetch updated list to get new title/snippet
        try {
            const freshPage = await NotesAPI.fetchNotes(0, NotesState.searchQuery);
            NotesState.notes = freshPage.items;
            NotesState.notesOffset = freshPage.items.length;
            NotesState.notesHasMore = freshPage.hasMore;
            NotesState.notesCursor = freshPage.nextCursor;
            this.sortNotesState();
            this.renderCurrentNotesList();
        } catch (error) {
            console.warn('Failed to refresh sidebar:', error);
        }
    },
});
