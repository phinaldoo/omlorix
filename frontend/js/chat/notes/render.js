// Render Functions
// ============================================================================

const NotesRender = {
    sidebarSkeleton() {
        return `
            <div class="notes-sidebar-skeleton">
                ${[1, 2, 3].map(() => `
                    <div class="notes-skeleton-item">
                        <div class="notes-skeleton-text"></div>
                        <div class="notes-skeleton-date"></div>
                    </div>
                `).join('')}
            </div>
        `;
    },

    getNoteTitle(content, maxLength = 30) {
        const plainText = NotesUtils.toPlainText(content);
        if (!plainText) {
            return notesT('notes_accept_untitled', 'Untitled note');
        }
        const firstLine = plainText.split('\n')[0].trim();
        if (firstLine.length <= maxLength) {
            return firstLine || notesT('notes_accept_untitled', 'Untitled note');
        }
        return firstLine.substring(0, maxLength) + '…';
    },

    formatDate(dateString) {
        if (!dateString) return '';
        const date = new Date(dateString);
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());

        if (dateOnly.getTime() === today.getTime()) {
            return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        }
        if (dateOnly.getTime() === yesterday.getTime()) {
            return notesT('notes_yesterday', 'Yesterday');
        }
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    },

    dropdownItem({ action, noteId, icon = '', label, isDanger = false, isShared = false }) {
        const classes = [
            'select-dropdown-button',
            isDanger ? 'select-dropdown-button-red' : '',
            isShared ? 'notes-dropdown-button-shared' : '',
        ].filter(Boolean).join(' ');

        return `
            <div class="select-dropdown-item">
                <button type="button" class="${classes}" data-action="${this.escapeHtml(action)}" data-note-id="${this.escapeHtml(noteId)}">
                    ${icon}
                    <span>${this.escapeHtml(label)}</span>
                </button>
            </div>
        `;
    },

    noteDropdownOptions(note, { includeSubscriberCount = true } = {}) {
        const noteId = note.id;
        const isSubscribed = note.is_subscribed === true;
        const activeShareId = note.clone_share_id || note.live_share_id || note.collaborate_share_id;
        const allowShare = canManageNoteSharing(note);
        const isShared = !!activeShareId && !isSubscribed;

        if (isSubscribed) {
            return this.dropdownItem({
                action: 'unsubscribe',
                noteId,
                icon: Icons.error,
                label: notesT('notes_remove_from_workspace', 'Remove from workspace'),
                isDanger: true,
            });
        }

        if (allowShare) {
            const subscriberCount = note.subscriber_count;
            const shareLabel = isShared
                ? (includeSubscriberCount && subscriberCount
                    ? notesFormatT('notes_share_shared_count', 'Shared ({count})', { count: subscriberCount })
                    : notesT('notes_share_shared', 'Shared'))
                : notesT('notes_share_action', 'Share');

            return `
                ${this.dropdownItem({
                    action: 'share',
                    noteId,
                    icon: Icons.connections,
                    label: shareLabel,
                    isShared,
                })}
                ${this.dropdownItem({
                    action: 'delete',
                    noteId,
                    icon: Icons?.trash || '',
                    label: notesT('notes_delete_title', 'Delete Note'),
                    isDanger: true,
                })}
            `;
        }

        return this.dropdownItem({
            action: 'delete',
            noteId,
            icon: Icons?.trash || '',
            label: notesT('notes_delete_title', 'Delete Note'),
            isDanger: true,
        });
    },

    noteItem(note, isActive) {
        const title = note.title || notesT('notes_accept_untitled', 'Untitled note');
        const dateStr = this.formatDate(note.updated_at);
        const preview = note.snippet || '';
        const isSubscribed = note.is_subscribed === true;
        const dropdownOptions = this.noteDropdownOptions(note);
        
        // Add subscribed badge if applicable
        let ownerBadge = '';
        if (isSubscribed) {
            const canEditBadge = note.share_type === 'collaborate' ? `<span class="notes-can-edit-badge">${this.escapeHtml(notesT('notes_share_can_edit_badge', 'can edit'))}</span>` : '';
            const shareTypeBadge = note.share_type === 'collaborate'
                ? notesT('notes_share_collab_badge', 'collab')
                : notesT('notes_share_live_badge', 'live');
            const ownerText = notesFormatT('notes_shared_by_owner', 'by {owner}', {
                owner: note.owner_name || notesT('notes_unknown_owner', 'Unknown'),
            });
            ownerBadge = `<span class="notes-subscribed-badge">${this.escapeHtml(ownerText)} <span class="notes-share-type-badge ${note.share_type}">${this.escapeHtml(shareTypeBadge)}</span>${canEditBadge}</span>`;
        }
        return `
            <div class="notes-list-item ${isActive ? 'active' : ''}${isSubscribed ? ' subscribed' : ''}" 
                 data-note-id="${note.id}" 
                 data-is-subscribed="${isSubscribed}">
                <button type="button" class="notes-list-item-select-btn" data-note-id="${note.id}"
                        aria-label="${this.escapeHtml(title)}" aria-pressed="${isActive}">
                    <span class="notes-list-item-content">
                        <span class="notes-list-item-title">${this.escapeHtml(title)}${ownerBadge}</span>
                        <span class="notes-list-item-preview">${preview ? this.escapeHtml(preview) : `<span class="notes-preview-empty">${this.escapeHtml(notesT('notes_no_additional_text', 'No additional text'))}</span>`}</span>
                        <span class="notes-list-item-date">${dateStr}</span>
                    </span>
                </button>
                <button type="button" class="notes-list-item-menu-btn" data-note-id="${note.id}" aria-label="${this.escapeHtml(notesT('notes_options_aria', 'Note options'))}">
                    ${Icons.ellipsisVertical}
                </button>
                <div class="select-dropdown" data-note-dropdown data-note-id="${note.id}">
                    ${dropdownOptions}
                </div>
            </div>
        `;
    },

    getPreview(content, maxLength = 60) {
        const plainText = NotesUtils.toPlainText(content);
        if (!plainText) {
            return notesT('notes_no_additional_text', 'No additional text');
        }
        const lines = plainText.split('\n');
        // Get second line or rest of first line for preview
        let preview = lines.length > 1 ? lines.slice(1).join(' ').trim() : '';
        if (!preview) {
            preview = lines[0].trim();
        }
        if (preview.length <= maxLength) {
            return preview || notesT('notes_no_additional_text', 'No additional text');
        }
        return preview.substring(0, maxLength) + '…';
    },

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
};

// ============================================================================
