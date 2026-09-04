// ============================================================================
// API Helpers
// ============================================================================

const isNoteSharingAllowed = () => {
    if (typeof window === 'undefined') return true;
    return window.allowNoteShareFeature !== false;
};

function noteHasExistingShareState(note) {
    if (!note) return false;
    return Boolean(note.clone_share_id || note.live_share_id || note.collaborate_share_id || Number(note.subscriber_count || 0) > 0);
}

function canManageNoteSharing(note) {
    return isNoteSharingAllowed() || noteHasExistingShareState(note);
}

// Serialize client-originated writes per note. Navigation snapshots, debounce
// saves, and manual saves can otherwise reach the API concurrently and turn a
// harmless slow request into a stale last-writer overwrite or conflict.
const noteUpdateQueues = new Map();

function normalizeNoteRevisionToken(value) {
    if (value === undefined || value === null) return '';
    return String(value).trim();
}

/**
 * Preserve HTTP metadata on Notes failures so callers can distinguish an
 * optimistic-lock conflict from connectivity, permission, and validation
 * failures. FastAPI may return either a string or a structured `detail` value.
 */
async function buildNotesApiError(response, fallbackMessage) {
    let payload = null;
    try {
        payload = await response.json();
    } catch (_) {
        payload = null;
    }
    const detail = payload?.detail;
    const serverMessage = typeof detail === 'string'
        ? detail
        : (typeof detail?.message === 'string' ? detail.message : '');
    const error = new Error(serverMessage || fallbackMessage);
    error.name = response.status === 409 ? 'NoteRevisionConflictError' : 'NotesApiError';
    error.status = response.status;
    error.code = detail?.code || (response.status === 409 ? 'note_revision_conflict' : 'notes_api_error');
    error.payload = payload;
    return error;
}

function isNoteRevisionConflict(error) {
    return Boolean(error && error.status === 409 && error.code === 'note_revision_conflict');
}

function isNoteFileReferenceUnavailable(error) {
    return Boolean(error && error.code === 'note_file_reference_unavailable');
}

function normalizeNoteFileReferenceIssue(error) {
    const detail = error?.payload?.detail;
    const reference = detail?.reference;
    if (!reference || typeof reference !== 'object') return null;
    const rawToken = String(reference.raw_token || '').trim();
    const fileId = String(reference.file_id || '').trim();
    if (!rawToken || !fileId) return null;
    return {
        kind: String(reference.kind || 'file').trim().toLowerCase(),
        owner_id: String(reference.owner_id || '').trim(),
        file_id: fileId,
        label: String(reference.label || '').trim(),
        raw_token: rawToken,
        occurrence: Number(reference.occurrence || 0) || 0,
    };
}

function replaceFirstNoteReferenceToken(content, rawToken, replacement = '') {
    const source = String(content ?? '');
    const target = String(rawToken || '');
    if (!target) return null;
    const markerIndex = source.indexOf(target);
    if (markerIndex < 0) return null;
    return `${source.slice(0, markerIndex)}${String(replacement ?? '')}${source.slice(markerIndex + target.length)}`;
}

const NotesAPI = {
    async request(input, init) {
        if (typeof window !== 'undefined' && typeof window.authedFetch === 'function') {
            return window.authedFetch(input, init);
        }
        return fetch(input, init);
    },

    async fetchNotes(offset = 0, query = '', cursor = null) {
        const response = await this.request(buildNotesListUrl(offset, query, cursor), {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_fetch_notes', 'Failed to fetch notes'));
        return normalizeNotesPage(await response.json(), offset);
    },

    async fetchNoteContent(noteId) {
        const normalizedNoteId = String(noteId || '').trim();
        const response = await this.request(`/api/v1/notes/${encodeURIComponent(normalizedNoteId)}/content`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_fetch_note_content', 'Failed to fetch note content'));
        return response.json();
    },

    async createNote(content = '') {
        const response = await this.request('/api/v1/notes/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ content }),
        });
        if (!response.ok) throw new Error(notesT('notes_error_create_note', 'Failed to create note'));
        return response.json();
    },

    updateNote(noteId, content, expectedUpdatedAt = null) {
        const normalizedNoteId = String(noteId || '').trim();
        const requestedRevision = normalizeNoteRevisionToken(expectedUpdatedAt);
        if (!requestedRevision) {
            return Promise.reject(new Error(notesT('notes_revision_required', 'Reload the note before saving changes.')));
        }
        const priorWrite = noteUpdateQueues.get(normalizedNoteId) || null;
        const priorPromise = priorWrite?.promise || Promise.resolve(null);
        const request = priorPromise
            .catch(() => undefined)
            .then(async (priorUpdatedNote) => {
                // Two local snapshots can be queued against the same base
                // revision while the first request is in flight. Only in that
                // specific chain may the later request advance to the revision
                // produced by its predecessor. A revision fetched by another
                // tab is never substituted here, so stale content still 409s.
                const canAdvanceLocalChain = Boolean(
                    priorWrite
                    && requestedRevision
                    && priorWrite.baseRevision === requestedRevision
                    && priorUpdatedNote?.updated_at
                );
                const expectedRevision = canAdvanceLocalChain
                    ? normalizeNoteRevisionToken(priorUpdatedNote.updated_at)
                    : requestedRevision;
                const payload = { content, expected_updated_at: expectedRevision };
                const response = await this.request(`/api/v1/notes/${encodeURIComponent(normalizedNoteId)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify(payload),
                });
                if (!response.ok) {
                    throw await buildNotesApiError(
                        response,
                        notesT('notes_error_update_note', 'Failed to update note'),
                    );
                }
                return response.json();
            });
        const trackedRequest = request.finally(() => {
            if (noteUpdateQueues.get(normalizedNoteId)?.promise === trackedRequest) {
                noteUpdateQueues.delete(normalizedNoteId);
            }
        });
        noteUpdateQueues.set(normalizedNoteId, {
            promise: trackedRequest,
            baseRevision: requestedRevision,
        });
        return trackedRequest;
    },

    async deleteNote(noteId, expectedUpdatedAt) {
        const revision = normalizeNoteRevisionToken(expectedUpdatedAt);
        if (!revision) throw new Error(notesT('notes_revision_required', 'Reload the note before changing it.'));
        const response = await this.request(`/api/v1/notes/${encodeURIComponent(noteId)}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ expected_updated_at: revision }),
        });
        if (!response.ok) throw await buildNotesApiError(response, notesT('notes_error_delete_note', 'Failed to delete note'));
        return response.json();
    },

    async fetchWorkspaceFiles(search = '', offset = 0) {
        const params = new URLSearchParams({
            limit: String(NOTES_FILE_PAGE_LIMIT),
            offset: String(offset),
            sort_field: 'name',
            sort_direction: 'asc',
        });
        if (search && search.trim()) {
            params.set('search', search.trim());
        }

        const response = await this.request(`/api/v1/files/workspace?${params.toString()}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_fetch_uploaded_files', 'Failed to fetch uploaded files'));
        return response.json();
    },

    async uploadFile(file) {
        const formData = new FormData();
        formData.append('file', file);
        const response = await this.request('/api/v1/files/upload', {
            method: 'POST',
            credentials: 'include',
            body: formData,
        });

        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }

        if (!response.ok || payload?.status !== 'success') {
            throw new Error(payload?.detail || payload?.message || notesT('notes_error_upload_file', 'Failed to upload file'));
        }
        return payload;
    },

    async fetchFile(fileId) {
        const response = await this.request(`/api/v1/files/${encodeURIComponent(fileId)}`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_fetch_uploaded_file', 'Failed to fetch uploaded file'));
        return response.json();
    },

    // Sharing APIs
    async shareNote(noteId, shareType = 'live') {
        const response = await this.request('/api/v1/notes/share', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ note_id: noteId, share_type: shareType }),
        });
        if (!response.ok) throw new Error(notesT('notes_error_share_note', 'Failed to share note'));
        return response.json();
    },

    async getShareStatus(noteId) {
        const response = await this.request(`/api/v1/notes/share/status?note_id=${encodeURIComponent(noteId)}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_share_status', 'Failed to get share status'));
        return response.json();
    },

    async deleteShare(noteId, shareType = null) {
        const body = { note_id: noteId };
        if (shareType) body.share_type = shareType;
        const response = await this.request('/api/v1/notes/share/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error(notesT('notes_error_remove_sharing', 'Failed to remove sharing'));
        return response.json();
    },

    async getSharedNotePreview(shareId) {
        const response = await this.request(`/api/v1/notes/shared/${encodeURIComponent(shareId)}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) {
            let detail = notesT('notes_error_shared_note_not_found', 'Shared note not found');
            try {
                const errorBody = await response.json();
                if (errorBody?.detail) detail = errorBody.detail;
            } catch (_) { /* ignore parse errors */ }
            const error = new Error(detail);
            error.status = response.status;
            throw error;
        }
        return response.json();
    },

    async acceptSharedNote(shareId) {
        const response = await this.request(`/api/v1/notes/shared/${encodeURIComponent(shareId)}/accept`, {
            method: 'POST',
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_accept_shared_note', 'Failed to accept shared note'));
        return response.json();
    },

    async cloneNote(shareId) {
        const response = await this.request(`/api/v1/notes/clone/${encodeURIComponent(shareId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_clone_note', 'Failed to clone note'));
        return response.json();
    },

    async unsubscribeFromNote(noteId) {
        const response = await this.request(`/api/v1/notes/shared/${encodeURIComponent(noteId)}/unsubscribe`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_unsubscribe', 'Failed to unsubscribe'));
        return response.json();
    },

    async fetchPublicUsers() {
        const users = [];
        const seenUserIds = new Set();
        let offset = 0;
        const limit = 100;
        while (true) {
            const response = await this.request(`/api/v1/users/public-users?limit=${limit}&offset=${offset}`, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
            });
            if (!response.ok) throw new Error(notesT('notes_share_load_users_failed', 'Failed to load users.'));
            const page = await response.json();
            const pageUsers = Array.isArray(page) ? page : [];
            pageUsers.forEach((user) => {
                const userId = String(user?.id || '').trim();
                if (!userId || seenUserIds.has(userId)) return;
                seenUserIds.add(userId);
                users.push(user);
            });
            const hasMore = String(response.headers.get('X-Has-More') || '').toLowerCase() === 'true';
            if (!hasMore || pageUsers.length === 0) break;
            offset += pageUsers.length;
        }
        return users;
    },

    async inviteUsersToNote(noteId, userIds, shareType = 'live') {
        const response = await this.request('/api/v1/notes/invite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ item_id: noteId, user_ids: userIds, share_type: shareType }),
        });
        if (!response.ok) throw new Error(notesT('notes_share_invite_failed', 'Failed to send invitations'));
        return response.json();
    },

    // History APIs
    async getNoteHistory(noteId, limit = 50, offset = 0) {
        const response = await this.request(`/api/v1/notes/${noteId}/history?limit=${limit}&offset=${offset}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_fetch_history', 'Failed to fetch note history'));
        return response.json();
    },

    async getHistoryEntry(noteId, historyId) {
        const response = await this.request(`/api/v1/notes/${noteId}/history/${historyId}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_error_fetch_history_entry', 'Failed to fetch history entry'));
        return response.json();
    },

    async restoreFromHistory(noteId, historyId, expectedUpdatedAt) {
        const response = await this.request(`/api/v1/notes/${noteId}/restore`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                history_id: historyId,
                expected_updated_at: normalizeNoteRevisionToken(expectedUpdatedAt),
            }),
        });
        if (!response.ok) throw new Error(notesT('notes_error_restore_note', 'Failed to restore note'));
        return response.json();
    },

    async downloadNote(noteId, format = 'md') {
        const normalizedFormat = String(format || 'md').trim().toLowerCase() === 'pdf' ? 'pdf' : 'md';
        const response = await this.request(`/api/v1/notes/${encodeURIComponent(noteId)}/download?format=${encodeURIComponent(normalizedFormat)}`, {
            method: 'GET',
            credentials: 'include',
        });
        if (!response.ok) throw new Error(notesT('notes_download_failed', 'Failed to prepare note download.'));
        return response.blob();
    },

};

// ============================================================================
