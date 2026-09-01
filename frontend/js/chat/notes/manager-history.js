/**
 * Notes workspace version-history methods.
 */

Object.assign(NotesManager, {
    // ============================================================================
    // Edit History Methods
    // ============================================================================

    /**
     * Open the version history modal for a note.
     * The panel is created lazily on first use and reused afterwards.
     */
    async showHistoryPanel(noteId = null) {
        const targetNoteId = noteId || NotesState.selectedNoteId;
        if (!targetNoteId) return;

        // Create history panel if it doesn't exist yet
        let panel = document.getElementById('notesHistoryPanel');
        if (!panel) {
            panel = this.createHistoryPanel();
            document.body.appendChild(panel);
        }

        // A close transition may still be waiting to hide this reused panel.
        // Cancel it before making the panel visible again so an older callback
        // cannot hide the newly opened dialog.
        clearTimeout(this._historyHideTimer);
        this._historyHideTimer = null;

        // Remember the opener before focus moves into the modal so closing the
        // panel returns keyboard users to the control they were using.
        this._historyPreviousFocus = document.activeElement;

        // Show panel
        NotesState.historyPanelOpen = true;
        NotesState.historyNoteId = targetNoteId;
        NotesState.selectedHistoryId = null;
        NotesState.historyPreviewContent = null;
        NotesState.historyEntryRequestToken = null;
        NotesState.historyEntries = [];
        NotesState.historyHasMore = false;
        NotesState.historyLoadingMore = false;
        panel.removeAttribute('hidden');
        panel.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
        requestAnimationFrame(() => panel.classList.add('active'));

        // Close the dialog with Escape while it is open
        if (this._historyKeyHandler) {
            document.removeEventListener('keydown', this._historyKeyHandler);
        }
        this._historyKeyHandler = (e) => {
            if (e.key === 'Escape') {
                // The restore confirmation is layered above history and owns
                // Escape while it is visible.
                if (NotesDOM.restoreOverlay && !NotesDOM.restoreOverlay.hidden) return;
                e.stopPropagation();
                this.hideHistoryPanel();
                return;
            }
            if (e.key === 'Tab') {
                const dialog = panel.querySelector('[role="dialog"]');
                const focusable = Array.from(dialog?.querySelectorAll(
                    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
                ) || []).filter((element) => !element.hidden && element.getClientRects().length > 0);
                if (!focusable.length) {
                    e.preventDefault();
                    dialog?.focus({ preventScroll: true });
                    return;
                }
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (e.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
                    e.preventDefault();
                    last.focus();
                } else if (!e.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
                    e.preventDefault();
                    first.focus();
                }
            }
        };
        document.addEventListener('keydown', this._historyKeyHandler);

        // Move focus into the dialog for keyboard and screen reader users
        const closeBtn = panel.querySelector('#notesHistoryCloseBtn');
        if (closeBtn) closeBtn.focus();

        // Load history entries
        await this.loadNoteHistory(targetNoteId);
    },

    /**
     * Build the version history modal DOM.
     *
     * Layout:
     *   - slim header with title, version count and close button
     *   - version list on the left
     *   - detail pane on the right with a Preview/Changes segmented switch
     *     and a Restore action for older versions
     */
    createHistoryPanel() {
        const panel = document.createElement('div');
        panel.id = 'notesHistoryPanel';
        panel.className = 'notes-history-panel shared-modal-overlay';
        panel.setAttribute('aria-hidden', 'true');
        panel.innerHTML = `
            <div class="notes-history-container shared-modal shared-modal--large shared-modal--fixed" role="dialog" aria-modal="true" aria-labelledby="notesHistoryTitle" tabindex="-1">
                <div class="notes-history-header shared-modal-header shared-modal-header--main">
                    <div class="notes-history-heading shared-modal-heading">
                        <h3 class="shared-modal-title" id="notesHistoryTitle">${NotesRender.escapeHtml(notesT('notes_history_title', 'Version History'))}</h3>
                        <span class="notes-history-count" id="notesHistoryCount"></span>
                    </div>
                    <button type="button" class="notes-history-close shared-modal-close" id="notesHistoryCloseBtn" aria-label="${NotesRender.escapeHtml(notesT('common_close', 'Close'))}">
                        ${Icons.close}
                    </button>
                </div>
                <div class="notes-history-body shared-modal-body">
                    <div class="notes-history-list" id="notesHistoryList">
                        <div class="notes-history-loading">
                            <div class="notes-history-spinner"></div>
                            <span>${NotesRender.escapeHtml(notesT('notes_history_loading', 'Loading history...'))}</span>
                        </div>
                    </div>
                    <div class="notes-history-detail">
                        <div class="notes-history-detail-bar">
                            <div class="notes-history-detail-info" id="notesHistoryPreviewTitle">
                                <span class="notes-history-detail-version">${NotesRender.escapeHtml(notesT('notes_history_select_version', 'Select a version to preview'))}</span>
                            </div>
                            <div class="notes-history-detail-actions">
                                <div class="notes-history-tabs" role="tablist">
                                    <button type="button" role="tab" id="notesHistoryTabPreview" class="notes-history-tab active" aria-selected="true" data-view="preview">
                                        ${NotesRender.escapeHtml(notesT('notes_history_tab_preview', 'Preview'))}
                                    </button>
                                    <button type="button" role="tab" id="notesHistoryTabChanges" class="notes-history-tab" aria-selected="false" data-view="diff">
                                        ${NotesRender.escapeHtml(notesT('notes_history_tab_changes', 'Changes'))}
                                        <span class="notes-history-diff-stats" id="notesHistoryDiffStats" hidden></span>
                                    </button>
                                </div>
                                <button type="button" class="notes-history-restore-btn" id="notesHistoryRestoreBtn" hidden>
                                    ${Icons.refresh}
                                    <span>${NotesRender.escapeHtml(notesT('notes_history_restore', 'Restore'))}</span>
                                </button>
                            </div>
                        </div>
                        <div class="notes-history-detail-body">
                            <div class="notes-history-view notes-history-preview" id="notesHistoryPreviewContent" role="tabpanel" aria-labelledby="notesHistoryTabPreview">
                                <div class="notes-history-placeholder">
                                    ${Icons.file}
                                    <p>${NotesRender.escapeHtml(notesT('notes_history_preview_empty', 'Select a version from the list to preview its content'))}</p>
                                </div>
                            </div>
                            <div class="notes-history-view notes-history-diff" id="notesHistoryDiffContent" role="tabpanel" aria-labelledby="notesHistoryTabChanges" hidden></div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Wire up close interactions (button + backdrop click)
        panel.querySelector('#notesHistoryCloseBtn').addEventListener('click', () => this.hideHistoryPanel());
        panel.addEventListener('click', (e) => { if (e.target === panel) this.hideHistoryPanel(); });

        // Restore action (opens the confirmation dialog)
        panel.querySelector('#notesHistoryRestoreBtn').addEventListener('click', () => this.confirmRestoreVersion());

        // Preview/Changes segmented switch
        panel.querySelectorAll('.notes-history-tab').forEach((tab) => {
            tab.addEventListener('click', () => this.switchHistoryView(tab.dataset.view));
        });

        return panel;
    },

    /** Close the version history modal and clean up its state. */
    hideHistoryPanel() {
        const panel = document.getElementById('notesHistoryPanel');
        if (panel) {
            panel.classList.remove('active');
            panel.setAttribute('aria-hidden', 'true');
            clearTimeout(this._historyHideTimer);
            this._historyHideTimer = setTimeout(() => {
                panel.setAttribute('hidden', '');
                this._historyHideTimer = null;
            }, 200);
        }
        document.body.classList.remove('modal-open');
        // Remove the Escape key listener registered on open
        if (this._historyKeyHandler) {
            document.removeEventListener('keydown', this._historyKeyHandler);
            this._historyKeyHandler = null;
        }
        NotesState.historyPanelOpen = false;
        NotesState.historyNoteId = null;
        NotesState.historyLoading = false;
        NotesState.historyLoadingMore = false;
        NotesState.historyHasMore = false;
        NotesState.historyRequestToken = null;
        NotesState.historyEntryRequestToken = null;
        NotesState.selectedHistoryId = null;
        NotesState.historyPreviewContent = null;
        this._noteHistoryInfiniteObserver?.disconnect();
        const previousFocus = this._historyPreviousFocus;
        this._historyPreviousFocus = null;
        if (previousFocus?.isConnected && typeof previousFocus.focus === 'function') {
            previousFocus.focus({ preventScroll: true });
        }
    },

    /**
     * Toggle the right pane between the rendered markdown preview
     * and the line diff of the selected version.
     */
    switchHistoryView(view) {
        const showDiff = view === 'diff';
        const previewEl = document.getElementById('notesHistoryPreviewContent');
        const diffEl = document.getElementById('notesHistoryDiffContent');
        const previewTab = document.getElementById('notesHistoryTabPreview');
        const changesTab = document.getElementById('notesHistoryTabChanges');

        if (previewEl) previewEl.hidden = showDiff;
        if (diffEl) diffEl.hidden = !showDiff;
        if (previewTab) {
            previewTab.classList.toggle('active', !showDiff);
            previewTab.setAttribute('aria-selected', String(!showDiff));
        }
        if (changesTab) {
            changesTab.classList.toggle('active', showDiff);
            changesTab.setAttribute('aria-selected', String(showDiff));
        }
    },

    /**
     * Reset the detail pane back to its neutral state
     * (used when the panel opens or reloads for a note).
     */
    resetHistoryDetail() {
        const infoEl = document.getElementById('notesHistoryPreviewTitle');
        if (infoEl) {
            infoEl.innerHTML = `<span class="notes-history-detail-version">${NotesRender.escapeHtml(notesT('notes_history_select_version', 'Select a version to preview'))}</span>`;
        }
        const previewEl = document.getElementById('notesHistoryPreviewContent');
        if (previewEl) {
            previewEl.innerHTML = `
                <div class="notes-history-placeholder">
                    ${Icons.file}
                    <p>${NotesRender.escapeHtml(notesT('notes_history_preview_empty', 'Select a version from the list to preview its content'))}</p>
                </div>
            `;
        }
        const diffEl = document.getElementById('notesHistoryDiffContent');
        if (diffEl) diffEl.innerHTML = '';
        const restoreBtn = document.getElementById('notesHistoryRestoreBtn');
        if (restoreBtn) restoreBtn.hidden = true;
        const statsEl = document.getElementById('notesHistoryDiffStats');
        if (statsEl) {
            statsEl.hidden = true;
            statsEl.innerHTML = '';
            statsEl.removeAttribute('title');
        }
        this.switchHistoryView('preview');
    },

    /** Fetch the edit history for a note and populate the version list. */
    async loadNoteHistory(noteId, { append = false } = {}) {
        if (append && (NotesState.historyLoadingMore || !NotesState.historyHasMore)) return;
        const requestToken = append ? NotesState.historyRequestToken : Symbol('note-history');
        if (!append) NotesState.historyRequestToken = requestToken;
        // A newly requested list invalidates any detail request from the
        // previously displayed note or version.
        NotesState.historyEntryRequestToken = null;
        if (append) NotesState.historyLoadingMore = true;
        else NotesState.historyLoading = true;
        const listEl = document.getElementById('notesHistoryList');
        const countEl = document.getElementById('notesHistoryCount');
        const isCurrentRequest = () => (
            NotesState.historyRequestToken === requestToken
            && String(NotesState.historyNoteId) === String(noteId)
            && NotesState.historyPanelOpen
        );

        if (!append) this.resetHistoryDetail();
        if (!append && countEl) countEl.textContent = '';
        if (!append && listEl) {
            listEl.innerHTML = `
                <div class="notes-history-loading">
                    <div class="notes-history-spinner"></div>
                    <span>${NotesRender.escapeHtml(notesT('notes_history_loading', 'Loading history...'))}</span>
                </div>
            `;
        }

        try {
            const offset = append ? NotesState.historyEntries.length : 0;
            const data = await NotesAPI.getNoteHistory(noteId, NOTES_HISTORY_PAGE_LIMIT, offset);
            // A user can switch notes (or close and reopen the panel) while
            // this request is in flight. Never let that response replace the
            // newer note's history.
            if (!isCurrentRequest()) return;
            NotesState.historyEntries = append
                ? this.appendUniqueById(NotesState.historyEntries, data.entries)
                : data.entries;
            NotesState.historyTotalCount = data.total_count;
            NotesState.historyHasMore = Boolean(data.has_more);

            if (countEl) {
                countEl.textContent = notesPluralT(
                    'notes_history_version_count',
                    data.total_count,
                    '{count} version',
                    '{count} versions'
                );
            }

            this.renderHistoryList(NotesState.historyEntries);

            // Auto-select the newest version so the panel is never empty
            if (!append && data.entries && data.entries.length > 0) {
                this.selectHistoryEntry(data.entries[0].id);
            }
        } catch (error) {
            if (!isCurrentRequest()) return;
            console.error('Failed to load history:', error);
            if (!append && listEl) {
                listEl.innerHTML = `
                    <div class="notes-history-empty">
                        <p>${NotesRender.escapeHtml(notesT('notes_error_fetch_history', 'Failed to fetch note history'))}</p>
                    </div>
                `;
            }
        } finally {
            if (NotesState.historyRequestToken === requestToken) {
                NotesState.historyLoading = false;
            }
            NotesState.historyLoadingMore = false;
        }
    },

    async loadMoreNoteHistory() {
        if (!NotesState.historyNoteId) return;
        await this.loadNoteHistory(NotesState.historyNoteId, { append: true });
    },

    /** Render the version list in the left column of the history modal. */
    renderHistoryList(entries) {
        const listEl = document.getElementById('notesHistoryList');
        if (!listEl) return;

        if (!entries || entries.length === 0) {
            listEl.innerHTML = `
                <div class="notes-history-empty">
                    ${Icons.clock}
                    <p>${NotesRender.escapeHtml(notesT('notes_history_empty_title', 'No edit history yet'))}</p>
                    <span>${NotesRender.escapeHtml(notesT('notes_history_empty_text', 'Changes will appear here as you edit'))}</span>
                </div>
            `;
            return;
        }

        const historyItemsHtml = entries.map((entry, index) => {
            const isLatest = index === 0;
            const date = new Date(entry.created_at);
            const timeStr = this.formatHistoryTime(date);
            const dateStr = this.formatHistoryDate(date);
            const versionLabel = isLatest
                ? notesT('notes_history_current_version', 'Current Version')
                : notesFormatT('notes_history_version_label', 'Version {version}', { version: entry.version_number });
            const isAssistant = entry.actor_type === 'assistant';
            const summary = entry.change_summary || '';

            return `
                <button type="button" class="notes-history-item${isLatest ? ' latest' : ''}"
                        data-history-id="${NotesRender.escapeHtml(String(entry.id))}"
                        data-version="${NotesRender.escapeHtml(String(entry.version_number))}">
                    <span class="notes-history-item-top">
                        <span class="notes-history-item-version">${NotesRender.escapeHtml(versionLabel)}</span>
                        <span class="notes-history-item-time">${NotesRender.escapeHtml(`${dateStr} · ${timeStr}`)}</span>
                    </span>
                    <span class="notes-history-item-meta">
                        <span class="notes-history-item-avatar" data-actor-type="${isAssistant ? 'assistant' : 'user'}" aria-hidden="true">
                            ${isAssistant ? Icons.skills_management : NotesRender.escapeHtml(this.getInitials(entry.user_display_name))}
                        </span>
                        <span class="notes-history-item-user">${NotesRender.escapeHtml(entry.user_display_name || '')}${isAssistant ? ` · ${NotesRender.escapeHtml(notesT('notes_history_ai_assistant', 'AI Assistant'))}` : ''}</span>
                    </span>
                    ${summary ? `<span class="notes-history-item-summary">${NotesRender.escapeHtml(summary)}</span>` : ''}
                </button>
            `;
        }).join('');
        const loadMoreHtml = NotesState.historyHasMore
            ? `<button type="button" class="notes-history-load-more" id="notesHistoryLoadMoreBtn">${NotesRender.escapeHtml(notesT('notes_history_load_more', 'Load older versions'))}</button>`
            : '';
        listEl.innerHTML = historyItemsHtml + loadMoreHtml;
        this.setupNoteHistoryInfiniteScroll();

        // Select a version on click
        listEl.querySelectorAll('.notes-history-item').forEach(item => {
            item.addEventListener('click', () => {
                this.selectHistoryEntry(item.dataset.historyId);
            });
        });
        listEl.querySelector('#notesHistoryLoadMoreBtn')?.addEventListener('click', () => this.loadMoreNoteHistory());
    },

    setupNoteHistoryInfiniteScroll() {
        this._noteHistoryInfiniteObserver?.disconnect();
        const list = document.getElementById('notesHistoryList');
        if (!list || !NotesState.historyHasMore || typeof IntersectionObserver !== 'function') return;
        const sentinel = list.querySelector('#notesHistoryLoadMoreBtn');
        if (!sentinel) return;
        this._noteHistoryInfiniteObserver = new IntersectionObserver((entries) => {
            if (entries[0]?.isIntersecting) this.loadMoreNoteHistory();
        }, { root: list, rootMargin: '120px', threshold: 0 });
        this._noteHistoryInfiniteObserver.observe(sentinel);
    },

    /** Build up-to-two-letter initials for the avatar bubble in the list. */
    getInitials(name) {
        if (!name) return '??';
        const parts = name.trim().split(' ');
        if (parts.length >= 2) {
            return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
        }
        return name.substring(0, 2).toUpperCase();
    },

    /** Format a history timestamp as a localized short time (e.g. 19:40). */
    formatHistoryTime(date) {
        return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    },

    /** Format a history date as Today/Yesterday or a localized short date. */
    formatHistoryDate(date) {
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());

        if (dateOnly.getTime() === today.getTime()) {
            return notesT('notes_due_today', 'Today');
        }
        if (dateOnly.getTime() === yesterday.getTime()) {
            return notesT('notes_yesterday', 'Yesterday');
        }
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    },

    /**
     * Load and display a single history entry in the detail pane:
     * markdown preview, diff against the previous version, and restore action.
     */
    async selectHistoryEntry(historyId) {
        const id = String(historyId);
        const noteId = NotesState.historyNoteId;
        if (!noteId || !NotesState.historyPanelOpen) return;

        const requestToken = Symbol('note-history-entry');
        NotesState.historyEntryRequestToken = requestToken;
        NotesState.selectedHistoryId = historyId;
        const isCurrentRequest = () => (
            NotesState.historyEntryRequestToken === requestToken
            && String(NotesState.historyNoteId) === String(noteId)
            && String(NotesState.selectedHistoryId) === id
            && NotesState.historyPanelOpen
        );

        // Highlight the selected entry in the list
        document.querySelectorAll('.notes-history-item').forEach(item => {
            const isActive = item.dataset.historyId === id;
            item.classList.toggle('active', isActive);
            if (isActive) {
                item.setAttribute('aria-current', 'true');
            } else {
                item.removeAttribute('aria-current');
            }
        });

        const infoEl = document.getElementById('notesHistoryPreviewTitle');
        const previewEl = document.getElementById('notesHistoryPreviewContent');
        const diffEl = document.getElementById('notesHistoryDiffContent');
        const restoreBtn = document.getElementById('notesHistoryRestoreBtn');
        const statsEl = document.getElementById('notesHistoryDiffStats');

        // Loading state while the version content is fetched
        if (previewEl) {
            previewEl.innerHTML = `
                <div class="notes-history-loading">
                    <div class="notes-history-spinner"></div>
                    <span>${NotesRender.escapeHtml(notesT('notes_history_loading_version', 'Loading version...'))}</span>
                </div>
            `;
        }
        if (diffEl) diffEl.innerHTML = '';

        try {
            const entry = await NotesAPI.getHistoryEntry(noteId, historyId);

            // Ignore stale responses when the note, panel, or selected version
            // changed while the entry was loading.
            if (!isCurrentRequest()) return;

            NotesState.historyPreviewContent = entry;
            const isLatest = String(NotesState.historyEntries[0]?.id) === id;

            // Detail bar: version label, author and timestamp
            if (infoEl) {
                const versionLabel = isLatest
                    ? notesT('notes_history_current_version', 'Current Version')
                    : notesFormatT('notes_history_version_label', 'Version {version}', { version: entry.version_number });
                const date = new Date(entry.created_at);
                const meta = `${notesFormatT('notes_shared_by_owner', 'by {owner}', { owner: entry.user_display_name || '' })} · ${this.formatHistoryDate(date)}, ${this.formatHistoryTime(date)}`;
                infoEl.innerHTML = `
                    <span class="notes-history-detail-version">${NotesRender.escapeHtml(versionLabel)}</span>
                    <span class="notes-history-detail-meta">${NotesRender.escapeHtml(meta)}</span>
                `;
            }

            // Rendered markdown preview of the selected version
            if (previewEl) {
                previewEl.innerHTML = '<div class="notes-history-render markdown-body"></div>';
                NotesPreview.render(
                    previewEl.querySelector('.notes-history-render'),
                    entry.content || '',
                    noteId,
                    NotesState.referencedFiles,
                );
            }

            // Restore is only offered for older versions of editable notes
            if (restoreBtn) {
                restoreBtn.hidden = isLatest || !NotesState.canEditCurrentNote;
            }

            // Diff of this version against its predecessor
            const hasPrevious = entry.previous_content !== null && entry.previous_content !== undefined;
            if (hasPrevious) {
                const stats = this.renderDiff(entry.previous_content, entry.content);
                if (statsEl) {
                    statsEl.hidden = false;
                    statsEl.innerHTML = `<span class="added">+${stats.added}</span><span class="removed">−${stats.removed}</span>`;
                    statsEl.setAttribute('title', notesHistoryDiffStatsT(stats.added, stats.removed));
                }
            } else {
                // Initial version: nothing to compare against
                if (diffEl) {
                    diffEl.innerHTML = `<div class="notes-diff-empty">${NotesRender.escapeHtml(notesT('notes_history_first_version', 'This is the first version — there are no earlier changes to compare.'))}</div>`;
                }
                if (statsEl) {
                    statsEl.hidden = true;
                    statsEl.innerHTML = '';
                    statsEl.removeAttribute('title');
                }
            }
        } catch (error) {
            if (!isCurrentRequest()) return;
            console.error('Failed to load history entry:', error);
            if (previewEl) {
                previewEl.innerHTML = `
                    <div class="notes-history-error">
                        <p>${NotesRender.escapeHtml(notesT('notes_error_fetch_history_entry_short', 'Failed to load this version'))}</p>
                    </div>
                `;
            }
        }
    },

    /**
     * Render the diff for the notes history modal.
     * Returns { added, removed } line counts for the stats badge.
     */
    renderDiff(oldContent, newContent) {
        const diffContent = document.getElementById('notesHistoryDiffContent');
        return this.renderDiffInto(diffContent, oldContent, newContent);
    },

    /**
     * Render a line diff between two texts into the given container.
     * Long runs of unchanged lines are collapsed behind an expandable row,
     * and modified line pairs get intra-line highlights.
     * Kept reusable for note-history views that need a standalone diff target.
     * Returns { added, removed } line counts for the stats badge.
     */
    renderDiffInto(diffContent, oldContent, newContent) {
        const ops = this.computeLineDiff(oldContent || '', newContent || '');

        let added = 0;
        let removed = 0;
        ops.forEach(op => {
            if (op.type === 'add') added++;
            else if (op.type === 'del') removed++;
        });

        if (!diffContent) return { added, removed };

        if (added === 0 && removed === 0) {
            diffContent.innerHTML = `<div class="notes-diff-empty">${NotesRender.escapeHtml(notesT('notes_history_no_differences', 'No differences'))}</div>`;
            return { added, removed };
        }

        const CONTEXT = 3;       // unchanged lines kept visible around each change
        const MIN_COLLAPSE = 5;  // minimum hidden lines required to fold a run

        const html = [];
        let i = 0;
        while (i < ops.length) {
            if (ops[i].type === 'equal') {
                // Collect the full run of unchanged lines
                let j = i;
                while (j < ops.length && ops[j].type === 'equal') j++;
                const run = ops.slice(i, j);
                const lead = i === 0 ? 0 : CONTEXT;          // context after the previous change
                const tail = j === ops.length ? 0 : CONTEXT; // context before the next change
                const hiddenCount = run.length - lead - tail;

                if (hiddenCount >= MIN_COLLAPSE) {
                    // Keep some context visible and fold the middle of the run
                    run.slice(0, lead).forEach(op => html.push(this.renderDiffLine(op)));
                    const hiddenHtml = run.slice(lead, run.length - tail).map(op => this.renderDiffLine(op)).join('');
                    html.push(`
                        <div class="notes-diff-fold">
                            <button type="button" class="notes-diff-expand">
                                <span>${NotesRender.escapeHtml(notesPluralT('notes_history_unchanged_lines', hiddenCount, '{count} unchanged line', '{count} unchanged lines'))}</span>
                            </button>
                            <div class="notes-diff-hidden">${hiddenHtml}</div>
                        </div>
                    `);
                    run.slice(run.length - tail).forEach(op => html.push(this.renderDiffLine(op)));
                } else {
                    run.forEach(op => html.push(this.renderDiffLine(op)));
                }
                i = j;
            } else {
                // Collect a block of consecutive changed lines (removals + additions)
                const dels = [];
                const adds = [];
                let j = i;
                while (j < ops.length && ops[j].type !== 'equal') {
                    if (ops[j].type === 'del') dels.push(ops[j]);
                    else adds.push(ops[j]);
                    j++;
                }
                // Pair removals with additions to highlight intra-line changes
                const pairs = Math.min(dels.length, adds.length);
                dels.forEach((op, k) => {
                    const inner = k < pairs ? this.diffWordHtml(op.text, adds[k].text)[0] : null;
                    html.push(this.renderDiffLine(op, inner));
                });
                adds.forEach((op, k) => {
                    const inner = k < pairs ? this.diffWordHtml(dels[k].text, op.text)[1] : null;
                    html.push(this.renderDiffLine(op, inner));
                });
                i = j;
            }
        }

        diffContent.innerHTML = `<div class="notes-diff">${html.join('')}</div>`;

        // Expand folded unchanged sections on demand
        diffContent.querySelectorAll('.notes-diff-expand').forEach(btn => {
            btn.addEventListener('click', () => {
                btn.closest('.notes-diff-fold')?.classList.add('expanded');
            });
        });

        return { added, removed };
    },

    /**
     * Render one diff line. `innerHtml` optionally carries pre-escaped HTML
     * with intra-line change highlights; plain text is escaped here.
     */
    renderDiffLine(op, innerHtml = null) {
        const cls = op.type === 'add' ? 'added' : op.type === 'del' ? 'removed' : 'unchanged';
        const marker = op.type === 'add' ? '+' : op.type === 'del' ? '−' : '';
        const text = innerHtml !== null ? innerHtml : NotesRender.escapeHtml(op.text);
        return `<div class="notes-diff-line ${cls}"><span class="notes-diff-marker" aria-hidden="true">${marker}</span><span class="notes-diff-text">${text}</span></div>`;
    },

    /**
     * Highlight the changed middle section of a modified line pair by
     * trimming the common prefix and suffix. Returns [oldHtml, newHtml]
     * (already HTML-escaped). Falls back to plain escaped text when the
     * lines share nothing, to avoid highlighting entire lines.
     */
    diffWordHtml(oldLine, newLine) {
        const esc = (t) => NotesRender.escapeHtml(t);

        // Common prefix length
        let prefix = 0;
        const maxShared = Math.min(oldLine.length, newLine.length);
        while (prefix < maxShared && oldLine[prefix] === newLine[prefix]) prefix++;

        // Common suffix length (must not overlap the prefix)
        let suffix = 0;
        while (
            suffix < maxShared - prefix &&
            oldLine[oldLine.length - 1 - suffix] === newLine[newLine.length - 1 - suffix]
        ) suffix++;

        // No meaningful overlap: skip intra-line highlighting entirely
        if (prefix === 0 && suffix === 0) {
            return [esc(oldLine), esc(newLine)];
        }

        const mark = (mid) => (mid ? `<mark class="notes-diff-word">${esc(mid)}</mark>` : '');
        const oldHtml = esc(oldLine.slice(0, prefix))
            + mark(oldLine.slice(prefix, oldLine.length - suffix))
            + esc(oldLine.slice(oldLine.length - suffix));
        const newHtml = esc(newLine.slice(0, prefix))
            + mark(newLine.slice(prefix, newLine.length - suffix))
            + esc(newLine.slice(newLine.length - suffix));
        return [oldHtml, newHtml];
    },

    /**
     * Compute a line-level diff between two texts.
     * Common prefix/suffix lines are trimmed first, then the remaining
     * middle is diffed with a longest-common-subsequence algorithm.
     * Returns an ordered list of ops: { type: 'equal'|'add'|'del', text }.
     */
    computeLineDiff(oldText, newText) {
        const oldLines = oldText.split('\n');
        const newLines = newText.split('\n');

        // Trim the common prefix
        let start = 0;
        while (start < oldLines.length && start < newLines.length && oldLines[start] === newLines[start]) {
            start++;
        }

        // Trim the common suffix (without crossing the prefix)
        let oldEnd = oldLines.length;
        let newEnd = newLines.length;
        while (oldEnd > start && newEnd > start && oldLines[oldEnd - 1] === newLines[newEnd - 1]) {
            oldEnd--;
            newEnd--;
        }

        let ops = oldLines.slice(0, start).map(text => ({ type: 'equal', text }));
        const midOld = oldLines.slice(start, oldEnd);
        const midNew = newLines.slice(start, newEnd);
        const maxLcsLinesPerSide = 10000;

        // LCS is O(n*m); guard against pathological sizes and fall back to a
        // plain replace block for extremely large changes.
        if (
            midOld.length
            && midNew.length
            && midOld.length <= maxLcsLinesPerSide
            && midNew.length <= maxLcsLinesPerSide
            && midOld.length * midNew.length <= 500000
        ) {
            // Concatenation avoids passing a potentially large operation list
            // as individual function arguments.
            ops = ops.concat(this.lcsDiffOps(midOld, midNew));
        } else {
            midOld.forEach(text => ops.push({ type: 'del', text }));
            midNew.forEach(text => ops.push({ type: 'add', text }));
        }

        oldLines.slice(oldEnd).forEach(text => ops.push({ type: 'equal', text }));
        return ops;
    },

    /**
     * Classic dynamic-programming LCS diff over two arrays of lines.
     * Produces properly aligned equal/del/add ops so that insertions do not
     * shift the rest of the document out of alignment.
     */
    lcsDiffOps(a, b) {
        const n = a.length;
        const m = b.length;
        const width = m + 1;
        // table[i][j] = length of the LCS of a[i..] and b[j..]
        const table = new Uint32Array((n + 1) * width);
        for (let i = n - 1; i >= 0; i--) {
            for (let j = m - 1; j >= 0; j--) {
                table[i * width + j] = a[i] === b[j]
                    ? table[(i + 1) * width + j + 1] + 1
                    : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
            }
        }

        // Backtrack through the table to emit ops in order
        const ops = [];
        let i = 0;
        let j = 0;
        while (i < n && j < m) {
            if (a[i] === b[j]) {
                ops.push({ type: 'equal', text: a[i] });
                i++;
                j++;
            } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) {
                ops.push({ type: 'del', text: a[i] });
                i++;
            } else {
                ops.push({ type: 'add', text: b[j] });
                j++;
            }
        }
        while (i < n) ops.push({ type: 'del', text: a[i++] });
        while (j < m) ops.push({ type: 'add', text: b[j++] });
        return ops;
    },

    showRestoreConfirmation() {
        const overlay = NotesDOM.restoreOverlay;
        const entry = NotesState.historyPreviewContent;
        const note = NotesState.notes.find(n => n.id === NotesState.historyNoteId);
        if (!overlay || !entry || !note) return;

        const description = document.getElementById('notesRestoreDescription');
        if (description) {
            const title = NotesRender.getNoteTitle(
                entry.content || note.title || notesT('notes_accept_untitled', 'Untitled note'),
                40
            );
            const isLatest = NotesState.historyEntries[0]?.id === entry.id;
            const version = isLatest
                ? notesT('notes_history_current_version_sentence', 'the current version')
                : notesFormatT('notes_history_version_label_lower', 'version {version}', { version: entry.version_number });
            description.textContent = notesFormatT(
                'notes_restore_description',
                'You are about to replace {title} with the content from {version}. The current content will be preserved as a new history entry.',
                { title, version }
            );
        }

        const authorEl = document.getElementById('notesRestoreAuthor');
        if (authorEl) {
            authorEl.textContent = entry.user_display_name || notesT('notes_unknown_owner', 'Unknown');
        }

        const timestampEl = document.getElementById('notesRestoreTimestamp');
        if (timestampEl) {
            const date = new Date(entry.created_at);
            timestampEl.textContent = `${date.toLocaleDateString()} • ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
        }

        const summaryEl = document.getElementById('notesRestoreSummary');
        if (summaryEl) {
            summaryEl.textContent = entry.change_summary || notesT('notes_history_edited_content', 'Edited note content');
        }

        const confirmBtn = document.getElementById('notesRestoreConfirmBtn');
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.classList.remove('loading');
        }

        overlay.removeAttribute('hidden');
        requestAnimationFrame(() => overlay.classList.add('active'));
    },

    hideRestoreConfirmation() {
        const overlay = NotesDOM.restoreOverlay;
        if (!overlay) return;
        overlay.classList.remove('active');
        setTimeout(() => overlay.setAttribute('hidden', ''), 200);
    },

    confirmRestoreVersion() {
        if (!NotesState.selectedHistoryId || !NotesState.historyNoteId || NotesState.isRestoringVersion) return;
        this.showRestoreConfirmation();
    },

    async executeRestoreVersion() {
        if (!NotesState.selectedHistoryId || !NotesState.historyNoteId || NotesState.isRestoringVersion) return;

        const entry = NotesState.historyPreviewContent;
        if (!entry) return;

        NotesState.isRestoringVersion = true;

        const overlayConfirmBtn = document.getElementById('notesRestoreConfirmBtn');
        if (overlayConfirmBtn) {
            overlayConfirmBtn.disabled = true;
            overlayConfirmBtn.classList.add('loading');
        }

        try {
            const result = await NotesAPI.restoreFromHistory(
                NotesState.historyNoteId,
                NotesState.selectedHistoryId,
                NotesState.currentNoteUpdatedAt,
            );

            // Update the note in state
            const noteIdx = NotesState.notes.findIndex(n => n.id === NotesState.historyNoteId);
            if (noteIdx >= 0) {
                NotesState.notes[noteIdx].content = entry.content;
            }

            // Update editor if this note is selected
            if (NotesState.selectedNoteId === NotesState.historyNoteId) {
                this.setCurrentEditorContent(entry.content || '', {
                    editable: NotesState.canEditCurrentNote,
                });
                NotesState.lastSavedContent = entry.content || '';
                NotesState.hasUnsavedChanges = false;
                try {
                    const refreshed = await NotesAPI.fetchNoteContent(NotesState.historyNoteId);
                    NotesState.currentNoteUpdatedAt = normalizeNoteRevisionToken(refreshed?.updated_at);
                    NotesState.referencedFiles = Array.isArray(refreshed?.referenced_files) ? refreshed.referenced_files : [];
                } catch (_) {
                    NotesState.referencedFiles = [];
                }
                this.renderEmbeddedFilesUi(entry.content || '');
            }

            this.hideRestoreConfirmation();
            this.hideHistoryPanel();
            this.renderCurrentNotesList();

            if (typeof showNotification === 'function') {
                showNotification(result.message || notesT('notes_restore_success', 'Note restored successfully'), 'success');
            }
        } catch (error) {
            console.error('Failed to restore version:', error);
            if (typeof showNotification === 'function') {
                showNotification(notesT('notes_restore_failed', 'Failed to restore version'), 'error');
            }
        } finally {
            NotesState.isRestoringVersion = false;

            if (overlayConfirmBtn) {
                overlayConfirmBtn.disabled = false;
                overlayConfirmBtn.classList.remove('loading');
            }
        }
    },
});

// ============================================================================
