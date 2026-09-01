/**
 * Notes Workspace Module
 * Full-featured notes management with auto-save functionality
 */

// ============================================================================
// State Management
// ============================================================================

const NotesState = {
    notes: [],
    selectedNoteId: null,
    isLoadingNotes: false,
    isLoadingContent: false,
    currentNoteContent: null,
    canEditCurrentNote: false,
    referencedFiles: [],
    initialized: false,
    // Auto-save state
    saveTimer: null,
    lastSavedContent: '',
    currentNoteUpdatedAt: '',
    isSaving: false,
    hasUnsavedChanges: false,
    referenceIssue: null,
    // Dropdown state
    openDropdownNoteId: null,
    // Auto-refresh polling state for shared notes
    refreshInterval: null,
    refreshIntervalMs: 5000,
    lastContentHash: null,
    refreshRequestToken: null,
    remoteUpdate: null,
    navigationBypass: false,
    // Search state
    searchQuery: '',
    searchResults: [],
    isSearching: false,
    notesOffset: 0,
    notesHasMore: false,
    notesLoadingMore: false,
    notesRequestToken: null,
    searchTimer: null,
    // History state
    historyPanelOpen: false,
    historyEntries: [],
    historyTotalCount: 0,
    historyLoading: false,
    historyLoadingMore: false,
    historyHasMore: false,
    historyPageSize: 50,
    historyNoteId: null,
    historyRequestToken: null,
    historyEntryRequestToken: null,
    selectedHistoryId: null,
    historyPreviewContent: null,
    isRestoringVersion: false,
    filePickerMode: null,
    filePickerFilter: 'all',
    filePickerSearch: '',
    filePickerFiles: [],
    filePickerFilteredFiles: [],
    filePickerSelection: new Set(),
    filePickerLoading: false,
    filePickerLoadingMore: false,
    filePickerOffset: 0,
    filePickerHasMore: false,
    filePickerTotal: 0,
    filePickerRequestToken: null,
    filePickerSearchTimer: null,
    filePickerError: '',
    filePickerLastFocused: null,
    recordingSource: 'microphone',
    recordingStream: null,
    recordingMediaRecorder: null,
    recordingChunks: [],
    recordingMimeType: '',
    recordingStartedAt: 0,
    recordingTimerId: null,
    recordingIsRecording: false,
    recordingIsUploading: false,
    recordingPendingFile: null,
    recordingDiscardOnStop: false,
    recordingLastFocused: null,
    sharingNoteId: null,
    shareMode: 'list',
    shareAction: 'link',
    shareStatus: null,
    currentShareType: 'live',
    currentCanEdit: false,
    publicUsers: [],
    publicUsersLoaded: false,
    publicUsersLoading: false,
    selectedUserIds: [],
    markdownEditor: null,
    markdownEditorEditable: null,
    markdownEditorNoteId: null,
    isDownloadingNote: false,
};

// Auto-save delay in milliseconds
const AUTOSAVE_DELAY = 500;
const NOTES_PAGE_LIMIT = 50;
const NOTES_HISTORY_PAGE_LIMIT = 50;
const NOTES_FILE_PAGE_LIMIT = 50;

function normalizeNotesPage(payload, fallbackOffset = 0) {
    const items = Array.isArray(payload) ? payload : (Array.isArray(payload?.items) ? payload.items : []);
    return {
        items,
        offset: Number(payload?.offset ?? fallbackOffset) || 0,
        hasMore: Array.isArray(payload) ? items.length >= NOTES_PAGE_LIMIT : Boolean(payload?.has_more),
    };
}

function buildNotesListUrl(offset = 0, query = '') {
    const params = new URLSearchParams({
        limit: String(NOTES_PAGE_LIMIT),
        offset: String(offset),
    });
    if (String(query || '').trim()) params.set('q', String(query).trim());
    return `/api/v1/notes/?${params.toString()}`;
}

function notesT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function notesFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(notesT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

/** Format a count with the locale's explicit plural-form translation key. */
function notesPluralT(baseKey, count, oneFallback, otherFallback) {
    let category = Number(count) === 1 ? 'one' : 'other';
    try {
        category = new Intl.PluralRules(document.documentElement?.lang || 'en').select(Math.abs(Number(count) || 0));
    } catch (_error) {
        // The one/other fallback above covers hosts without Intl support.
    }
    const fallback = category === 'one' ? oneFallback : otherFallback;
    return notesFormatT(`${baseKey}_${category}`, fallback, { count });
}

/** Build the history diff's accessible count label from two plural phrases. */
function notesHistoryDiffStatsT(added, removed) {
    return notesFormatT(
        'notes_history_diff_stats_aria',
        '{addedLabel}, {removedLabel}',
        {
            addedLabel: notesPluralT('notes_history_added_lines', added, '{count} line added', '{count} lines added'),
            removedLabel: notesPluralT('notes_history_removed_lines', removed, '{count} line removed', '{count} lines removed'),
        },
    );
}

function normalizeNoteReferenceSnippet(text) {
    return String(text || '').replace(/\r\n/g, '\n').trim();
}

/**
 * Package a marked Notes selection with stable tool metadata. Reference chips
 * are sent with the next chat message, allowing the assistant to target this
 * exact note and use a snippet edit instead of rewriting unrelated content.
 */
function buildNoteArtifactReferenceText({ text = '', noteId = '', title = '', source = 'note editor' } = {}) {
    const selectedText = normalizeNoteReferenceSnippet(text);
    const normalizedNoteId = String(noteId || '').trim();
    const normalizedTitle = String(title || '').trim() || notesT('notes_accept_untitled', 'Untitled note');
    const lines = [
        '[Notes artifact reference]',
        'Tool to edit: notes',
        `Note: ${normalizedTitle}`,
        'Content type: markdown',
    ];
    if (normalizedNoteId) lines.push(`note_id: ${normalizedNoteId}`);
    lines.push('Edit guidance: use the notes tool with type="edit" and note_id. For a local change, use the marked text as the exact start_snippet and end_snippet when possible.');
    lines.push(`Marked from: ${String(source || 'note editor')}`);
    lines.push('Marked text:');
    lines.push('```');
    lines.push(selectedText);
    lines.push('```');
    return lines.join('\n');
}

/** Fallback for pages where the shared reference-chip API is unavailable. */
function insertNoteReferenceIntoComposer(selectedText) {
    const input = document.getElementById('chatBoxInput');
    // The structured artifact envelope is an internal model-facing protocol.
    // When reference chips are unavailable, insert only the user's selection so
    // the composer never exposes hardcoded English implementation metadata.
    const text = normalizeNoteReferenceSnippet(selectedText);
    if (!input || !text) return false;

    const currentValue = String(input.value || '');
    const start = typeof input.selectionStart === 'number' ? input.selectionStart : currentValue.length;
    const end = typeof input.selectionEnd === 'number' ? input.selectionEnd : start;
    const prefix = start > 0 && currentValue[start - 1] && !/\s/.test(currentValue[start - 1]) ? ' ' : '';
    const insertedText = prefix + text;
    input.value = `${currentValue.slice(0, start)}${insertedText}${currentValue.slice(end)}`;
    const cursor = start + insertedText.length;
    input.setSelectionRange(cursor, cursor);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    return true;
}

function notifyNoteReferenceAdded() {
    const message = notesT('canvas_reference_added', 'Marked selection added as a reference');
    if (typeof window.notifySuccess === 'function') {
        window.notifySuccess(message);
    } else if (typeof showNotification === 'function') {
        showNotification(message, 'success');
    }
}

function notifyNoteReferenceError() {
    const message = notesT('canvas_reference_select_text_first', 'Select text in the artifact first');
    if (typeof window.notifyWarning === 'function') {
        window.notifyWarning(message);
    } else if (typeof showNotification === 'function') {
        showNotification(message, 'warning');
    }
}

/** Add a Notes selection to the same editable reference-chip flow as Canvas. */
function addNoteSelectionToChatReferences({ selectionData = null, noteId = '', title = '', source = 'note editor' } = {}) {
    const selectedText = normalizeNoteReferenceSnippet(selectionData?.text);
    const normalizedNoteId = String(noteId || '').trim();
    if (!selectedText || !normalizedNoteId) {
        notifyNoteReferenceError();
        return false;
    }

    const referenceText = buildNoteArtifactReferenceText({
        text: selectedText,
        noteId: normalizedNoteId,
        title,
        source,
    });
    if (typeof window.addReferencePart === 'function') {
        const existingReferences = typeof window.getSelectedReferenceParts === 'function'
            ? window.getSelectedReferenceParts()
            : [];
        if (Array.isArray(existingReferences) && existingReferences.includes(referenceText)) {
            // The shared helper owns the localized duplicate warning.
            window.addReferencePart(referenceText);
            return false;
        }
        window.addReferencePart(referenceText);
        notifyNoteReferenceAdded();
        return true;
    }

    const inserted = insertNoteReferenceIntoComposer(selectedText);
    if (inserted) notifyNoteReferenceAdded();
    return inserted;
}

async function waitForNoteSaveToSettle(isSavingFn, timeoutMs = 10000, pollIntervalMs = 50) {
    // Poll until an in-flight save clears so downloads can use the latest content.
    const startedAt = Date.now();
    while (typeof isSavingFn === 'function' && isSavingFn()) {
        if (Date.now() - startedAt >= timeoutMs) {
            return false;
        }
        await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
    }
    return true;
}

