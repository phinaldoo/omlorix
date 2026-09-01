/**
 * Shared Notes conflict recovery.
 *
 * Workspace Notes and the chat-side Notes editor both save complete Markdown
 * documents. This controller keeps the failed local snapshot independent from
 * either editor, renders a three-way comparison, and retries only against the
 * exact latest revision the user reviewed.
 */
(() => {
    const DB_NAME = 'omlorix-notes-recovery';
    const DB_VERSION = 1;
    const STORE_NAME = 'drafts';
    const memoryDrafts = new Map();
    let active = null;
    let previousFocus = null;
    let keyHandler = null;
    let closeTimer = null;
    let bodyHadModalOpen = false;

    const t = (key, fallback) => (
        typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback
    );

    const escapeHtml = (value) => String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');

    function openDatabase() {
        if (typeof indexedDB === 'undefined') return Promise.resolve(null);
        return new Promise((resolve) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = () => {
                if (!request.result.objectStoreNames.contains(STORE_NAME)) {
                    request.result.createObjectStore(STORE_NAME, { keyPath: 'noteId' });
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => resolve(null);
            request.onblocked = () => resolve(null);
        });
    }

    async function writeRecovery(record) {
        memoryDrafts.set(record.noteId, record);
        const db = await openDatabase();
        if (!db) return;
        await new Promise((resolve) => {
            const transaction = db.transaction(STORE_NAME, 'readwrite');
            transaction.objectStore(STORE_NAME).put(record);
            transaction.oncomplete = resolve;
            transaction.onerror = resolve;
            transaction.onabort = resolve;
        });
        db.close();
    }

    async function readRecovery(noteId) {
        const normalizedNoteId = String(noteId || '').trim();
        if (!normalizedNoteId) return null;
        if (memoryDrafts.has(normalizedNoteId)) return memoryDrafts.get(normalizedNoteId);
        const db = await openDatabase();
        if (!db) return null;
        const record = await new Promise((resolve) => {
            const request = db.transaction(STORE_NAME, 'readonly').objectStore(STORE_NAME).get(normalizedNoteId);
            request.onsuccess = () => resolve(request.result || null);
            request.onerror = () => resolve(null);
        });
        db.close();
        if (record) memoryDrafts.set(normalizedNoteId, record);
        return record;
    }

    async function deleteRecovery(noteId) {
        const normalizedNoteId = String(noteId || '').trim();
        memoryDrafts.delete(normalizedNoteId);
        const db = await openDatabase();
        if (!db) return;
        await new Promise((resolve) => {
            const transaction = db.transaction(STORE_NAME, 'readwrite');
            transaction.objectStore(STORE_NAME).delete(normalizedNoteId);
            transaction.oncomplete = resolve;
            transaction.onerror = resolve;
            transaction.onabort = resolve;
        });
        db.close();
    }

    /** Return line operations for a bounded LCS diff. */
    function lineDiffOps(oldText, newText) {
        const oldLines = String(oldText || '').split('\n');
        const newLines = String(newText || '').split('\n');
        let prefix = 0;
        while (prefix < oldLines.length && prefix < newLines.length && oldLines[prefix] === newLines[prefix]) prefix++;
        let oldEnd = oldLines.length;
        let newEnd = newLines.length;
        while (oldEnd > prefix && newEnd > prefix && oldLines[oldEnd - 1] === newLines[newEnd - 1]) {
            oldEnd--;
            newEnd--;
        }

        const before = oldLines.slice(0, prefix).map((text) => ({ type: 'equal', text }));
        const a = oldLines.slice(prefix, oldEnd);
        const b = newLines.slice(prefix, newEnd);
        const middle = [];
        if (!a.length || !b.length || a.length * b.length > 500000) {
            a.forEach((text) => middle.push({ type: 'del', text }));
            b.forEach((text) => middle.push({ type: 'add', text }));
        } else {
            const width = b.length + 1;
            const table = new Uint32Array((a.length + 1) * width);
            for (let i = a.length - 1; i >= 0; i--) {
                for (let j = b.length - 1; j >= 0; j--) {
                    table[i * width + j] = a[i] === b[j]
                        ? table[(i + 1) * width + j + 1] + 1
                        : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
                }
            }
            let i = 0;
            let j = 0;
            while (i < a.length && j < b.length) {
                if (a[i] === b[j]) {
                    middle.push({ type: 'equal', text: a[i++] });
                    j++;
                } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) {
                    middle.push({ type: 'del', text: a[i++] });
                } else {
                    middle.push({ type: 'add', text: b[j++] });
                }
            }
            while (i < a.length) middle.push({ type: 'del', text: a[i++] });
            while (j < b.length) middle.push({ type: 'add', text: b[j++] });
        }
        return before.concat(middle, oldLines.slice(oldEnd).map((text) => ({ type: 'equal', text })));
    }

    /** Convert base-to-variant diff operations into replacement ranges. */
    function diffHunks(baseContent, variantContent) {
        const hunks = [];
        const ops = lineDiffOps(baseContent, variantContent);
        let baseIndex = 0;
        let index = 0;
        while (index < ops.length) {
            if (ops[index].type === 'equal') {
                baseIndex++;
                index++;
                continue;
            }
            const start = baseIndex;
            const replacement = [];
            while (index < ops.length && ops[index].type !== 'equal') {
                if (ops[index].type === 'del') baseIndex++;
                if (ops[index].type === 'add') replacement.push(ops[index].text);
                index++;
            }
            hunks.push({ start, end: baseIndex, replacement });
        }
        return hunks;
    }

    function hunksOverlap(left, right) {
        const leftInsertion = left.start === left.end;
        const rightInsertion = right.start === right.end;
        if (leftInsertion && rightInsertion) return left.start === right.start;
        if (leftInsertion) return left.start >= right.start && left.start <= right.end;
        if (rightInsertion) return right.start >= left.start && right.start <= left.end;
        return left.start < right.end && right.start < left.end;
    }

    function sameHunk(left, right) {
        return left.start === right.start
            && left.end === right.end
            && left.replacement.join('\n') === right.replacement.join('\n');
    }

    /**
     * Conservatively merge independent line ranges. Overlapping edits are left
     * for the user; this routine never guesses an ordering that could erase a
     * collaborator's text.
     */
    function threeWayMerge(baseContent, localContent, serverContent) {
        if (localContent === serverContent) return { clean: true, content: localContent, conflicts: 0 };
        if (localContent === baseContent) return { clean: true, content: serverContent, conflicts: 0 };
        if (serverContent === baseContent) return { clean: true, content: localContent, conflicts: 0 };

        const localHunks = diffHunks(baseContent, localContent);
        const serverHunks = diffHunks(baseContent, serverContent);
        let conflicts = 0;
        localHunks.forEach((localHunk) => {
            serverHunks.forEach((serverHunk) => {
                if (hunksOverlap(localHunk, serverHunk) && !sameHunk(localHunk, serverHunk)) conflicts++;
            });
        });
        if (conflicts) return { clean: false, content: localContent, conflicts };

        const combined = [...serverHunks];
        localHunks.forEach((localHunk) => {
            if (!combined.some((serverHunk) => sameHunk(localHunk, serverHunk))) combined.push(localHunk);
        });
        combined.sort((left, right) => left.start - right.start || left.end - right.end);
        const baseLines = String(baseContent || '').split('\n');
        const output = [];
        let cursor = 0;
        combined.forEach((hunk) => {
            output.push(...baseLines.slice(cursor, hunk.start));
            output.push(...hunk.replacement);
            cursor = hunk.end;
        });
        output.push(...baseLines.slice(cursor));
        return { clean: true, content: output.join('\n'), conflicts: 0 };
    }

    function ensurePanel() {
        let panel = document.getElementById('notesConflictPanel');
        if (panel) return panel;
        panel = document.createElement('div');
        panel.id = 'notesConflictPanel';
        panel.className = 'notes-conflict-panel shared-modal-overlay';
        panel.hidden = true;
        panel.inert = true;
        panel.setAttribute('aria-hidden', 'true');
        panel.innerHTML = `
            <section class="notes-conflict-container shared-modal shared-modal--large shared-modal--fixed" role="dialog" aria-modal="true" aria-labelledby="notesConflictTitle" aria-describedby="notesConflictDescription" tabindex="-1">
                <header class="notes-conflict-header shared-modal-header shared-modal-header--main">
                    <div class="shared-modal-heading">
                        <h2 class="shared-modal-title" id="notesConflictTitle"></h2>
                        <p class="shared-modal-subtitle" id="notesConflictDescription"></p>
                    </div>
                    <button type="button" class="notes-conflict-close shared-modal-close" data-conflict-action="close" aria-label="${escapeHtml(t('common_close', 'Close'))}">${window.Icons?.close || '<span aria-hidden="true">×</span>'}</button>
                </header>
                <div class="notes-conflict-body shared-modal-body">
                    <div class="notes-conflict-status" id="notesConflictMergeStatus" role="status" aria-live="polite"></div>
                    <div class="notes-conflict-comparisons">
                        <section class="notes-conflict-comparison" aria-labelledby="notesConflictServerHeading">
                            <h3 id="notesConflictServerHeading"></h3>
                            <div id="notesConflictServerDiff" class="notes-conflict-diff"></div>
                        </section>
                        <section class="notes-conflict-comparison" aria-labelledby="notesConflictLocalHeading">
                            <h3 id="notesConflictLocalHeading"></h3>
                            <div id="notesConflictLocalDiff" class="notes-conflict-diff"></div>
                        </section>
                    </div>
                    <label class="notes-conflict-merge-label" for="notesConflictMergeEditor"></label>
                    <textarea id="notesConflictMergeEditor" class="notes-conflict-merge-editor" spellcheck="false"></textarea>
                    <div class="notes-conflict-error" id="notesConflictError" role="alert" hidden></div>
                </div>
                <footer class="notes-conflict-footer shared-modal-footer">
                    <div class="notes-conflict-recovery-actions">
                        <button type="button" class="notes-conflict-btn secondary om-button border cancel" data-conflict-action="copy"></button>
                        <button type="button" class="notes-conflict-btn secondary om-button border cancel" data-conflict-action="download"></button>
                    </div>
                    <div class="notes-conflict-resolution-actions">
                        <button type="button" class="notes-conflict-btn secondary om-button border cancel" data-conflict-action="server"></button>
                        <button type="button" class="notes-conflict-btn secondary om-button border cancel" data-conflict-action="reapply"></button>
                        <button type="button" class="notes-conflict-btn primary om-button border submit" data-conflict-action="merge"></button>
                    </div>
                </footer>
            </section>
        `;
        panel.addEventListener('click', (event) => {
            const action = event.target.closest('[data-conflict-action]')?.dataset.conflictAction;
            if (action) void handleAction(action);
        });
        document.body.appendChild(panel);
        return panel;
    }

    function setPanelCopy(panel) {
        panel.querySelector('#notesConflictTitle').textContent = t('notes_conflict_title', 'This note changed elsewhere');
        panel.querySelector('#notesConflictDescription').textContent = t(
            'notes_conflict_description',
            'Your draft is safe. Review the latest saved changes and your changes before choosing what to save.',
        );
        panel.querySelector('#notesConflictServerHeading').textContent = t('notes_conflict_server_changes', 'Latest server changes');
        panel.querySelector('#notesConflictLocalHeading').textContent = t('notes_conflict_local_changes', 'Your draft changes');
        panel.querySelector('.notes-conflict-merge-label').textContent = t('notes_conflict_merge_label', 'Merged draft');
        panel.querySelector('[data-conflict-action="copy"]').textContent = t('notes_conflict_copy_draft', 'Copy draft');
        panel.querySelector('[data-conflict-action="download"]').textContent = t('notes_conflict_download_draft', 'Download draft');
        panel.querySelector('[data-conflict-action="server"]').textContent = t('notes_conflict_use_latest', 'Use latest');
        panel.querySelector('[data-conflict-action="reapply"]').textContent = t('notes_conflict_reapply', 'Reapply my changes');
        panel.querySelector('[data-conflict-action="merge"]').textContent = t('notes_conflict_save_merge', 'Save merged draft');
    }

    function renderPlainDiff(container, oldContent, newContent) {
        const ops = lineDiffOps(oldContent, newContent);
        container.innerHTML = `<div class="notes-diff">${ops.map((op) => {
            const className = op.type === 'add' ? 'added' : op.type === 'del' ? 'removed' : 'unchanged';
            const marker = op.type === 'add' ? '+' : op.type === 'del' ? '−' : '';
            return `<div class="notes-diff-line ${className}"><span class="notes-diff-marker" aria-hidden="true">${marker}</span><span class="notes-diff-text">${escapeHtml(op.text)}</span></div>`;
        }).join('')}</div>`;
    }

    function render() {
        if (!active) return;
        if (closeTimer) {
            window.clearTimeout(closeTimer);
            closeTimer = null;
        }
        const panel = ensurePanel();
        setPanelCopy(panel);
        renderPlainDiff(panel.querySelector('#notesConflictServerDiff'), active.baseContent, active.serverContent);
        renderPlainDiff(panel.querySelector('#notesConflictLocalDiff'), active.baseContent, active.localContent);
        const merge = threeWayMerge(active.baseContent, active.localContent, active.serverContent);
        active.autoMerge = merge;
        panel.querySelector('#notesConflictMergeEditor').value = merge.content;
        const status = panel.querySelector('#notesConflictMergeStatus');
        status.className = `notes-conflict-status ${merge.clean ? 'clean' : 'overlap'}`;
        status.textContent = merge.clean
            ? t('notes_conflict_merge_clean', 'The changes do not overlap and can be reapplied automatically.')
            : t('notes_conflict_merge_overlap', 'Some changes overlap. Review and edit the merged draft before saving.');
        panel.querySelector('[data-conflict-action="reapply"]').disabled = !merge.clean;
        panel.querySelector('#notesConflictError').hidden = true;
        if (panel.hidden) {
            bodyHadModalOpen = document.body.classList.contains('modal-open');
        }
        panel.removeAttribute('hidden');
        panel.inert = false;
        panel.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
        requestAnimationFrame(() => panel.classList.add('active'));
        panel.querySelector('[data-conflict-action="close"]')?.focus();
    }

    function close({ resolved = false } = {}) {
        const panel = document.getElementById('notesConflictPanel');
        panel?.classList.remove('active');
        if (panel) {
            panel.inert = true;
            panel.setAttribute('aria-hidden', 'true');
        }
        if (closeTimer) window.clearTimeout(closeTimer);
        closeTimer = panel ? window.setTimeout(() => {
            panel.setAttribute('hidden', '');
            if (!bodyHadModalOpen) document.body.classList.remove('modal-open');
            bodyHadModalOpen = false;
            closeTimer = null;
        }, 180) : null;
        if (keyHandler) document.removeEventListener('keydown', keyHandler);
        keyHandler = null;
        const focusTarget = previousFocus;
        previousFocus = null;
        if (resolved) active = null;
        if (focusTarget?.isConnected) focusTarget.focus({ preventScroll: true });
    }

    async function copyDraft() {
        const text = String(active?.localContent || '');
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            const copied = document.execCommand('copy');
            textarea.remove();
            if (!copied) throw new Error('Copy command was rejected');
        }
        const button = document.querySelector('[data-conflict-action="copy"]');
        if (button) {
            button.textContent = t('notes_conflict_copied', 'Draft copied');
            window.setTimeout(() => {
                if (button.isConnected) button.textContent = t('notes_conflict_copy_draft', 'Copy draft');
            }, 1600);
        }
    }

    function downloadDraft() {
        const blob = new Blob([String(active?.localContent || '')], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `note-conflict-${String(active?.noteId || 'draft').slice(0, 36)}.md`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    function setBusy(busy) {
        const panel = ensurePanel();
        panel.querySelectorAll('button').forEach((button) => {
            button.disabled = Boolean(busy) || (button.dataset.conflictAction === 'reapply' && !active?.autoMerge?.clean);
        });
        panel.querySelector('#notesConflictMergeEditor').readOnly = Boolean(busy);
    }

    function showError(message) {
        const element = ensurePanel().querySelector('#notesConflictError');
        element.textContent = message;
        element.hidden = false;
    }

    async function refreshLatest() {
        const latest = await active.fetchLatest(active.noteId);
        active.serverContent = String(latest?.content || '');
        active.serverRevision = String(latest?.updated_at || '');
        active.serverSnapshot = latest;
        await writeRecovery({
            noteId: active.noteId,
            baseContent: active.baseContent,
            baseRevision: active.baseRevision,
            localContent: active.localContent,
            savedAt: Date.now(),
        });
        render();
    }

    async function saveResolution(content) {
        setBusy(true);
        try {
            const updated = await active.save(active.noteId, content, active.serverRevision);
            await deleteRecovery(active.noteId);
            try {
                active.onResolved?.({ content, updated, resolution: 'saved' });
            } catch (callbackError) {
                console.error('Failed to apply resolved note content:', callbackError);
            }
            close({ resolved: true });
            return true;
        } catch (error) {
            if (error?.status === 409) {
                await refreshLatest();
                showError(t('notes_conflict_changed_again', 'The note changed again while you were resolving it. The comparison has been refreshed.'));
                return false;
            }
            showError(error?.message || t('notes_error_save_note', 'Failed to save note'));
            return false;
        } finally {
            setBusy(false);
        }
    }

    async function handleAction(action) {
        if (!active) return;
        if (action === 'close') {
            close();
            active.onDeferred?.();
            return;
        }
        if (action === 'copy') {
            try {
                await copyDraft();
            } catch (_) {
                showError(t('notes_conflict_copy_failed', 'Could not copy the draft. Download it instead.'));
            }
            return;
        }
        if (action === 'download') {
            downloadDraft();
            return;
        }
        if (action === 'server') {
            const snapshot = active.serverSnapshot;
            await deleteRecovery(active.noteId);
            try {
                active.onResolved?.({ content: active.serverContent, updated: snapshot, resolution: 'server' });
            } catch (callbackError) {
                console.error('Failed to apply the latest note content:', callbackError);
            }
            close({ resolved: true });
            return;
        }
        if (action === 'reapply' && active.autoMerge?.clean) {
            await saveResolution(active.autoMerge.content);
            return;
        }
        if (action === 'merge') {
            const content = ensurePanel().querySelector('#notesConflictMergeEditor').value;
            await saveResolution(content);
        }
    }

    async function open(options) {
        const noteId = String(options?.noteId || '').trim();
        if (!noteId || typeof options?.fetchLatest !== 'function' || typeof options?.save !== 'function') return false;
        const localContent = String(options.localContent ?? '');
        const baseContent = String(options.baseContent ?? '');
        const record = {
            noteId,
            baseContent,
            baseRevision: String(options.baseRevision || ''),
            localContent,
            savedAt: Date.now(),
        };
        await writeRecovery(record);
        let latest = options.serverSnapshot || null;
        if (!latest) latest = await options.fetchLatest(noteId);
        active = {
            ...options,
            ...record,
            serverSnapshot: latest,
            serverContent: String(latest?.content || ''),
            serverRevision: String(latest?.updated_at || ''),
            autoMerge: null,
        };
        previousFocus = document.activeElement;
        if (keyHandler) document.removeEventListener('keydown', keyHandler);
        keyHandler = (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                void handleAction('close');
                return;
            }
            if (event.key !== 'Tab') return;
            const focusable = Array.from(ensurePanel().querySelectorAll('button:not(:disabled), textarea:not(:disabled)'));
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        };
        document.addEventListener('keydown', keyHandler);
        render();
        return true;
    }

    function reopen(noteId = '') {
        if (!active || (noteId && String(active.noteId) !== String(noteId))) return false;
        previousFocus = document.activeElement;
        render();
        return true;
    }

    function updateLocalDraft(noteId, localContent) {
        const normalizedNoteId = String(noteId || '').trim();
        if (!normalizedNoteId) return;
        if (active && String(active.noteId) === normalizedNoteId) {
            active.localContent = String(localContent ?? '');
            void writeRecovery({
                noteId: active.noteId,
                baseContent: active.baseContent,
                baseRevision: active.baseRevision,
                localContent: active.localContent,
                savedAt: Date.now(),
            });
        }
    }

    function updateServerSnapshot(noteId, snapshot) {
        if (!active || String(active.noteId) !== String(noteId) || !snapshot) return;
        active.serverSnapshot = snapshot;
        active.serverContent = String(snapshot.content || '');
        active.serverRevision = String(snapshot.updated_at || '');
        const panel = document.getElementById('notesConflictPanel');
        if (panel && !panel.hidden) render();
    }

    window.NotesConflictManager = {
        open,
        reopen,
        updateLocalDraft,
        updateServerSnapshot,
        close,
        getRecovery: readRecovery,
        deleteRecovery,
        threeWayMerge,
        isActiveFor: (noteId) => Boolean(active && String(active.noteId) === String(noteId)),
    };
})();
