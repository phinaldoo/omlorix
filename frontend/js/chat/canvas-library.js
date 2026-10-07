(() => {
    'use strict';
    const t = (key, fallback) => window.getTranslation?.(key, fallback) || fallback;
    let panel, active, offset = 0, selected, revision, requestId = 0, opener, previewTrack, previousInert;
    const el = (tag, text, className) => {
        const node = document.createElement(tag);
        if (text) node.textContent = text;
        if (className) node.className = className;
        return node;
    };
    const button = (text, action) => {
        const node = el('button', text, 'om-button');
        node.type = 'button';
        node.addEventListener('click', () => run(action));
        return node;
    };
    async function api(path, options) {
        const response = await window.authedFetch(`/api/v1/files/canvas/${encodeURIComponent(active.fileId)}${path}`, options);
        if (!response.ok) throw new Error(response.status === 409
            ? t('canvas_revision_conflict', 'This document changed. Reopen it before saving or restoring.')
            : t('canvas_library_failed', 'The document could not be updated. Please try again.'));
        return response.status === 204 ? null : response.json();
    }
    async function run(action) {
        if (!panel || panel.getAttribute('aria-busy') === 'true') return;
        const targetPanel = panel;
        targetPanel.setAttribute('aria-busy', 'true');
        try { await action(); }
        catch (error) {
            const message = targetPanel.querySelector('[role="status"]');
            if (message) message.textContent = error.message;
        } finally { targetPanel.setAttribute('aria-busy', 'false'); }
    }
    function close(restoreFocus = true) {
        requestId++;
        panel?.remove();
        panel = null;
        if (previewTrack) previewTrack.inert = previousInert;
        if (restoreFocus) opener?.focus();
    }
    async function loadHistory(append = false) {
        const id = ++requestId;
        const result = await api(`/history?offset=${offset}&limit=20`);
        if (id !== requestId || !panel) return;
        revision = result.current_revision;
        const list = panel.querySelector('.canvas-library-versions');
        if (!append) list.replaceChildren();
        for (const item of result.items) {
            const row = button(`${t('canvas_history_version', 'Version')} ${item.revision} · ${new Date(item.created_at).toLocaleString()}`, async () => {
                selected = item;
                const preview = panel.querySelector('textarea');
                preview.value = '';
                panel.querySelector('[data-restore]').disabled = false;
                list.querySelectorAll('button').forEach(node => node.setAttribute('aria-pressed', String(node === row)));
                if (item.content_type === 'spreadsheet') {
                    preview.value = t('canvas_history_binary', 'Download this version to inspect the spreadsheet.');
                    return;
                }
                const response = await window.authedFetch(`/api/v1/files/canvas/${encodeURIComponent(active.fileId)}/history/${encodeURIComponent(item.id)}/content?preview=true`);
                if (!response.ok) throw new Error(t('canvas_library_failed', 'The document could not be updated. Please try again.'));
                const text = await response.text();
                if (panel && selected === item) {
                    preview.value = text;
                    if (response.headers.get('X-Content-Truncated') === 'true') {
                        panel.querySelector('[role="status"]').textContent = t('files_preview_text_truncated', 'Preview truncated to the first {size}. Download the file to view everything.').replace('{size}', '256 KiB');
                    }
                }
            });
            row.setAttribute('aria-pressed', 'false');
            list.append(row);
        }
        if (!result.items.length && !append) list.append(el('p', t('canvas_history_empty', 'Earlier versions appear after your first edit.')));
        panel.querySelector('[data-more]').hidden = !result.has_more;
        offset += result.items.length;
    }
    async function showMembers(memberOffset = 0) {
        const area = panel.querySelector('.canvas-library-members');
        if (!memberOffset) area.replaceChildren();
        const result = await api(`/members?offset=${memberOffset}&limit=50`);
        if (!area.isConnected) return;
        area.querySelector('[data-members-more]')?.remove();
        if (!memberOffset) area.append(el('h4', t('canvas_access', 'Document access')));
        for (const member of result.items) {
            const row = el('div', '', 'canvas-library-member');
            row.append(el('span', `${member.email} · ${member.role === 'editor' ? t('canvas_access_editor', 'Can edit') : t('canvas_access_viewer', 'Can view')}`));
            row.append(button(t('canvas_access_remove', 'Remove access'), async () => {
                await api(`/members/${encodeURIComponent(member.user_id)}`, { method: 'DELETE' });
                await showMembers();
            }));
            area.append(row);
        }
        if (result.has_more) {
            const moreMembers = button(t('canvas_history_more', 'Load more'), () => showMembers(memberOffset + result.items.length));
            moreMembers.dataset.membersMore = '';
            area.append(moreMembers);
        }
        if (memberOffset) return;
        const form = el('form', '', 'canvas-library-member');
        const label = el('label', t('canvas_access_email', 'Email address'));
        const email = el('input');
        email.type = 'email'; email.required = true; email.autocomplete = 'email';
        label.append(email);
        const role = el('select');
        role.setAttribute('aria-label', t('canvas_access_role', 'Access level'));
        for (const [value, text] of [['viewer', t('canvas_access_viewer', 'Can view')], ['editor', t('canvas_access_editor', 'Can edit')]]) {
            const option = el('option', text); option.value = value; role.append(option);
        }
        const submit = el('button', t('canvas_access_add', 'Grant access'), 'om-button');
        submit.type = 'submit';
        form.append(label, role, submit);
        form.addEventListener('submit', event => {
            event.preventDefault();
            run(async () => {
                await api('/members', { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: email.value, role: role.value}) });
                await showMembers();
            });
        });
        area.append(form);
    }
    async function open(trigger) {
        if (panel) { close(); return; }
        const widget = window.canvasMarkdownWidget;
        active = widget?.getActiveDocument();
        if (!active?.fileId || !active.canEdit) return;
        if (!await widget.flushActiveDocument()) return;
        active = widget.getActiveDocument();
        opener = trigger; offset = 0; selected = null;
        panel = el('section', '', 'canvas-library-panel');
        panel.setAttribute('aria-label', t('canvas_history', 'Version history'));
        panel.tabIndex = -1;
        const heading = el('div', '', 'canvas-library-toolbar');
        const closeButton = el('button', t('common_close', 'Close'), 'om-button');
        closeButton.type = 'button';
        closeButton.addEventListener('click', () => close());
        heading.append(el('h3', t('canvas_history', 'Version history')), closeButton);
        const status = el('p'); status.setAttribute('role', 'status');
        const list = el('div', '', 'canvas-library-versions');
        const more = button(t('canvas_history_more', 'Load more'), () => loadHistory(true)); more.dataset.more = ''; more.hidden = true;
        const preview = el('textarea', '', 'canvas-library-source');
        preview.readOnly = true;
        preview.setAttribute('aria-label', t('canvas_history_preview', 'Version contents'));
        const actions = el('div', '', 'canvas-library-toolbar');
        const restore = button(t('canvas_history_restore', 'Restore version'), async () => {
            if (!selected) return;
            const targetPanel = panel;
            const versionId = selected.id;
            const accepted = await window.showWarningConfirm({
                title: t('canvas_history_restore', 'Restore version'),
                message: t('canvas_history_restore_confirm', 'Restore this version? Your current version will remain in history.'),
                confirmLabel: t('canvas_history_restore', 'Restore version'),
            });
            if (!accepted || panel !== targetPanel) return;
            await api('/restore', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({version_id: versionId, expected_revision: revision}) });
            close();
            await widget.reloadActiveDocument();
            window.FilesManager?.refresh?.();
        });
        restore.dataset.restore = ''; restore.disabled = true;
        const download = button(t('files_preview_download', 'Download'), async () => {
            if (!selected) return;
            const response = await window.authedFetch(`/api/v1/files/canvas/${encodeURIComponent(active.fileId)}/history/${encodeURIComponent(selected.id)}/content`);
            if (!response.ok) throw new Error(t('canvas_library_failed', 'The document could not be updated. Please try again.'));
            const url = URL.createObjectURL(await response.blob());
            const link = el('a'); link.href = url; link.download = selected.file_name; link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        });
        actions.append(restore, download);
        const members = el('div', '', 'canvas-library-members');
        panel.append(heading, status, list, more, preview, actions, members);
        panel.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); close(); } });
        previewTrack = document.getElementById('canvas-markdown-PreviewTrack');
        previousInert = previewTrack?.inert || false;
        if (previewTrack) previewTrack.inert = true;
        document.getElementById('canvas-markdown-PreviewPanel').append(panel);
        panel.focus();
        await run(async () => { await loadHistory(); if (panel && active.canManage) await showMembers(); });
    }
    document.addEventListener('click', event => {
        const trigger = event.target.closest('#canvas-library-History');
        if (trigger) void open(trigger);
    });
    document.addEventListener('canvas-document-changed', event => {
        if (panel && event.detail !== active?.fileId) close(false);
    });
})();
