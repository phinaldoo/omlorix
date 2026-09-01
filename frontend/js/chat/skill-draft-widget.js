(function () {
    'use strict';

    const CARD_SELECTOR = '.skill-draft-result-card';
    const FOLDER_ORDER = ['scripts', 'references', 'assets'];
    // Keep every translation key literal and discoverable by the i18n tooling.
    const FOLDER_LABELS = {
        scripts: { key: 'skill_draft_folder_scripts', fallback: 'Scripts' },
        references: { key: 'skill_draft_folder_references', fallback: 'References' },
        assets: { key: 'skill_draft_folder_assets', fallback: 'Assets' },
    };
    const VALID_FILENAME = /^[A-Za-z0-9._-]+$/;
    const DESKTOP_BREAKPOINT = 900;
    const RESIZE_STEP = 16;
    const RESIZE_LARGE_STEP = 48;

    const panel = document.getElementById('skillDraftPreviewPanel');
    const panelResizer = document.getElementById('skillDraftPreviewResizer');
    const panelClose = document.getElementById('skillDraftPreviewClose');
    const panelTitle = document.getElementById('skillDraftPreviewTitle');
    const panelHeaderStatus = document.getElementById('skillDraftPreviewHeaderStatus');
    const panelHeaderStatusText = document.getElementById('skillDraftPreviewHeaderStatusText');
    const panelViewToggle = document.getElementById('skillDraftPreviewViewToggle');
    const panelFileCount = document.getElementById('skillDraftFileCount');
    const panelFileList = document.getElementById('skillDraftFileList');
    const panelAddFile = document.getElementById('skillDraftAddFile');
    const panelEditor = document.getElementById('skillDraftEditor');
    const panelFooterStatus = document.getElementById('skillDraftFooterStatus');
    const panelFooterStatusText = document.getElementById('skillDraftFooterStatusText');
    const panelOpenWorkspace = document.getElementById('skillDraftOpenWorkspace');
    const panelSave = document.getElementById('skillDraftSave');

    let activeCard = null;
    let activeTrigger = null;
    let previewVisible = false;
    let resizeActive = false;
    let resizePointerId = null;

    /** Return translated copy while retaining a safe English bootstrap fallback. */
    function skillDraftT(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    /** Interpolate named variables with the application's translation helper. */
    function skillDraftTf(key, fallback, vars = {}) {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(skillDraftT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    /** Escape generated markup before placing user-controlled draft values in it. */
    function escapeHtml(value = '') {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** Resolve SVGs through the shared icon registry. */
    function getIcon(name) {
        const icons = window.Icons || {};
        if (name === 'sparkle') return icons.sparkle || icons.code || icons.file || '';
        return icons[name] || '';
    }

    /** Keep image previews restricted to URL forms that an image element needs. */
    function safeImageUrl(value) {
        const url = String(value || '').trim();
        if (/^(?:https?:\/\/|\/|data:image\/)/i.test(url)) return url;
        return '';
    }

    function getFolderLabel(folder) {
        const translation = FOLDER_LABELS[folder] || FOLDER_LABELS.references;
        return skillDraftT(translation.key, translation.fallback);
    }

    function humanizeSkillName(value) {
        const normalized = String(value || '').trim().replace(/[-_]+/g, ' ');
        return normalized ? normalized.replace(/\b\p{L}/gu, (letter) => letter.toUpperCase()) : '';
    }

    function createInlineFileId(prefix = 'file') {
        return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function getJsonStoreNode(card) {
        return card.querySelector('.skill-draft-widget-data[data-json-store="true"]')
            || card.querySelector('.skill-draft-widget-data');
    }

    /** Decode the inert payload re-injected by the chat sanitizer. */
    function parseDraftPayload(card) {
        const node = getJsonStoreNode(card);
        if (!node) {
            throw new Error(skillDraftT('skill_draft_missing_data', 'Missing skill draft data'));
        }
        const raw = String(node.textContent || '').trim();
        if (!raw) {
            throw new Error(skillDraftT('skill_draft_empty_data', 'Empty skill draft data'));
        }
        return JSON.parse(raw);
    }

    /** Create editable client state without mutating the persisted tool payload. */
    function buildInitialState(payload) {
        const items = [{
            id: 'skill-md',
            filename: 'SKILL.md',
            folder_type: null,
            kind: 'inline_text',
            content: String(payload.skill_markdown || ''),
            locked: true,
        }];

        for (const file of Array.isArray(payload.files) ? payload.files : []) {
            items.push({
                id: createInlineFileId('draft'),
                filename: String(file.filename || 'untitled.txt'),
                folder_type: String(file.folder_type || 'references'),
                kind: String(file.kind || 'inline_text'),
                content: typeof file.content === 'string' ? file.content : '',
                source_file_id: file.source_file_id ? String(file.source_file_id) : null,
                source_file_name: file.source_file_name ? String(file.source_file_name) : null,
                source_file_size: Number(file.source_file_size || file.size || 0),
                source_file_category: file.source_file_category ? String(file.source_file_category) : null,
                media_type: String(file.resolved_media_type || file.media_type || '').trim(),
                preview_url: file.preview_url ? String(file.preview_url) : '',
                description: file.description ? String(file.description) : '',
            });
        }

        return {
            draftId: String(payload.draft_id || ''),
            fallbackName: String(payload.name || ''),
            icon: String(payload.icon || ''),
            items,
            selectedId: 'skill-md',
            view: 'edit',
            dirty: false,
            saving: false,
            savedSkillId: '',
            savedTitle: '',
            statusKind: 'info',
            statusText: skillDraftT(
                'skill_draft_review_before_save',
                'Review the generated files before adding the skill to your workspace.',
            ),
        };
    }

    /** Parse the small scalar subset of Agent Skills frontmatter used in the UI. */
    function parseSkillMarkdown(markdown) {
        const source = String(markdown || '');
        const lines = source.split(/\r?\n/);
        if (!lines.length || lines[0].trim() !== '---') {
            return { error: skillDraftT('skill_draft_frontmatter_required', 'SKILL.md must start with a frontmatter block.') };
        }
        let endIndex = -1;
        for (let index = 1; index < lines.length; index += 1) {
            if (lines[index].trim() === '---') {
                endIndex = index;
                break;
            }
        }
        if (endIndex === -1) {
            return { error: skillDraftT('skill_draft_frontmatter_closing_required', 'SKILL.md frontmatter is missing a closing --- line.') };
        }

        const result = {};
        for (const line of lines.slice(1, endIndex)) {
            const match = line.match(/^([A-Za-z0-9_-]+)\s*:\s*(.*)$/);
            if (!match) continue;
            let value = match[2].trim();
            if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
                value = value.slice(1, -1);
            }
            result[match[1]] = value;
        }
        return {
            name: String(result.name || '').trim(),
            description: String(result.description || '').trim(),
            compatibility: String(result.compatibility || '').trim(),
            license: String(result.license || '').trim(),
            error: '',
        };
    }

    function stripFrontmatter(markdown) {
        const source = String(markdown || '');
        const match = source.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
        return match ? source.slice(match[0].length) : source;
    }

    function getSelectedItem(state) {
        return state.items.find((item) => item.id === state.selectedId) || state.items[0];
    }

    function getDraftDisplayName(state) {
        const parsed = parseSkillMarkdown(state.items[0]?.content || '');
        return humanizeSkillName(parsed.name || state.fallbackName)
            || skillDraftT('skill_draft_editor_title', 'New skill');
    }

    function formatBytes(size) {
        const value = Number(size || 0);
        if (!Number.isFinite(value) || value <= 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let index = 0;
        let current = value;
        while (current >= 1024 && index < units.length - 1) {
            current /= 1024;
            index += 1;
        }
        return `${current >= 10 || index === 0 ? current.toFixed(0) : current.toFixed(1)} ${units[index]}`;
    }

    /** Validate everything the save schema can reject before issuing a request. */
    function collectValidationErrors(state) {
        const errors = [];
        const seen = new Set();
        for (const item of state.items) {
            if (item.id === 'skill-md') continue;
            const folder = String(item.folder_type || '').trim();
            const name = String(item.filename || '').trim();
            if (!folder || !FOLDER_ORDER.includes(folder)) {
                errors.push(skillDraftT('skill_draft_file_folder_required', 'Every file must belong to Scripts, References, or Assets.'));
                continue;
            }
            if (!name) {
                errors.push(skillDraftT('skill_draft_file_filename_required', 'Every file needs a filename.'));
                continue;
            }
            if (!VALID_FILENAME.test(name) || name.includes('..')) {
                errors.push(skillDraftTf('skill_draft_filename_invalid', 'Invalid filename: {filename}', { filename: name }));
                continue;
            }
            if (item.kind !== 'source_file' && !String(item.content || '')) {
                errors.push(skillDraftTf('skill_draft_file_content_required', '{filename} needs content before the skill can be saved.', { filename: name }));
            }
            const key = `${folder.toLowerCase()}/${name.toLowerCase()}`;
            if (seen.has(key)) {
                errors.push(skillDraftTf('skill_draft_duplicate_file', 'Duplicate file detected: {path}', {
                    path: `${folder}/${name}`,
                }));
            }
            seen.add(key);
        }

        const parsed = parseSkillMarkdown(state.items[0]?.content || '');
        if (parsed.error) {
            errors.push(parsed.error);
        } else {
            if (!parsed.name) errors.push(skillDraftT('skill_draft_name_required', 'SKILL.md must include a name field.'));
            if (!parsed.description) errors.push(skillDraftT('skill_draft_description_required', 'SKILL.md must include a description field.'));
        }
        return errors;
    }

    function getFileMetaLabel(item) {
        if (item.id === 'skill-md') return skillDraftT('skill_draft_file_required', 'Required');
        if (item.kind === 'source_file') return skillDraftT('skill_draft_file_linked', 'Linked');
        return getFolderLabel(item.folder_type);
    }

    function getFileIcon(item) {
        if (item.id === 'skill-md') return getIcon('sparkle');
        if (item.folder_type === 'scripts') return getIcon('code');
        if (item.folder_type === 'assets') return getIcon('image');
        return getIcon('file');
    }

    /** Render the small chat card that launches the shared editor. */
    function renderCard(card) {
        const state = card?.__skillDraftState;
        if (!state) return;
        const isOpen = previewVisible && activeCard === card;
        const title = card.querySelector('[data-role="card-title"]');
        const summary = card.querySelector('[data-role="card-summary"]');
        const icon = card.querySelector('[data-role="card-icon"]');
        const openIcon = card.querySelector('[data-role="open-icon"]');
        const openLabel = card.querySelector('[data-role="open-label"]');
        const openButton = card.querySelector('[data-action="open-editor"]');

        if (title) title.textContent = getDraftDisplayName(state);
        if (summary) {
            const isSingleFile = state.items.length === 1;
            const key = state.savedSkillId
                ? (isSingleFile ? 'skill_draft_card_summary_saved_one' : 'skill_draft_card_summary_saved_other')
                : (isSingleFile ? 'skill_draft_card_summary_one' : 'skill_draft_card_summary_other');
            const fallback = state.savedSkillId
                ? (isSingleFile ? 'Skill draft · {count} file · Saved to workspace' : 'Skill draft · {count} files · Saved to workspace')
                : (isSingleFile ? 'Skill draft · {count} file' : 'Skill draft · {count} files');
            summary.textContent = skillDraftTf(key, fallback, { count: state.items.length });
        }
        if (icon) icon.innerHTML = state.savedSkillId ? getIcon('check') : getIcon('sparkle');
        if (openIcon) openIcon.innerHTML = getIcon('eye');
        if (openLabel) {
            openLabel.textContent = isOpen
                ? skillDraftT('skill_draft_close_editor', 'Close editor')
                : skillDraftT('skill_draft_open_editor', 'Open editor');
        }
        if (openButton) openButton.setAttribute('aria-expanded', String(isOpen));
        card.dataset.saved = state.savedSkillId ? 'true' : 'false';
    }

    function renderAllCards() {
        document.querySelectorAll(CARD_SELECTOR).forEach(renderCard);
    }

    function renderHeader(state) {
        if (!panelTitle || !panelHeaderStatus || !panelHeaderStatusText) return;
        const parsed = parseSkillMarkdown(state.items[0]?.content || '');
        panelTitle.textContent = getDraftDisplayName(state);

        let kind = 'info';
        const isSingleFile = state.items.length === 1;
        let text = skillDraftTf(
            isSingleFile ? 'skill_draft_status_draft_files_one' : 'skill_draft_status_draft_files_other',
            isSingleFile ? 'Draft · {count} file' : 'Draft · {count} files',
            { count: state.items.length },
        );
        if (parsed.error) {
            kind = 'error';
            text = skillDraftT('skill_draft_manifest_needs_attention', 'Manifest needs attention');
        } else if (state.savedSkillId) {
            kind = 'success';
            text = skillDraftT('skill_draft_status_saved_workspace', 'Saved to workspace');
        } else if (state.dirty) {
            kind = 'dirty';
            text = skillDraftT('skill_draft_status_unsaved', 'Unsaved changes');
        }
        const statusClass = {
            info: '',
            dirty: 'unsaved',
            success: 'complete',
            error: 'error',
        }[kind] || '';
        panelHeaderStatus.className = `skill-draft-preview-header-status canvas-markdown-preview-status ${statusClass}`.trim();
        panelHeaderStatusText.textContent = text;

        if (panelViewToggle) {
            const isManifest = state.selectedId === 'skill-md';
            panelViewToggle.hidden = !isManifest;
            panelViewToggle.querySelectorAll('[data-skill-draft-view]').forEach((button) => {
                const selected = button.dataset.skillDraftView === state.view;
                button.classList.toggle('is-active', selected);
                button.classList.toggle('active', selected);
                button.setAttribute('aria-selected', String(selected));
                button.tabIndex = selected ? 0 : -1;
            });
        }
    }

    function renderFiles(state) {
        if (!panelFileList || !panelFileCount) return;
        const isSingleFile = state.items.length === 1;
        panelFileCount.textContent = skillDraftTf(
            isSingleFile ? 'skill_draft_files_count_one' : 'skill_draft_files_count_other',
            isSingleFile ? '{count} file' : '{count} files',
            { count: state.items.length },
        );
        panelFileList.innerHTML = state.items.map((item) => {
            const active = item.id === state.selectedId;
            const removeLabel = skillDraftTf('skill_draft_remove_named_file_aria', 'Remove {filename}', {
                filename: item.filename,
            });
            const remove = item.locked ? '' : `
                <button type="button" class="skill-draft-file-row-remove om-button" data-remove-file="${escapeHtml(item.id)}" aria-label="${escapeHtml(removeLabel)}" title="${escapeHtml(skillDraftT('skill_draft_remove_file', 'Remove'))}"${state.savedSkillId ? ' disabled' : ''}>
                    ${getIcon('trash')}
                </button>`;
            return `
                <div class="skill-draft-file-row${active ? ' is-active' : ''}" data-locked="${item.locked ? 'true' : 'false'}">
                    <button type="button" class="skill-draft-file-row-select sidebar-element-button" data-select-file="${escapeHtml(item.id)}"${active ? ' aria-current="true"' : ''}>
                        <span class="skill-draft-file-row-icon" aria-hidden="true">${getFileIcon(item)}</span>
                        <span class="skill-draft-file-row-name">${escapeHtml(item.filename)}</span>
                        <span class="skill-draft-file-row-meta">${escapeHtml(getFileMetaLabel(item))}</span>
                    </button>
                    ${remove}
                </div>`;
        }).join('');
        if (panelAddFile) panelAddFile.disabled = Boolean(state.savedSkillId);
    }

    function buildEditorHeadMarkup(item, state) {
        const badge = item.id === 'skill-md'
            ? `<span class="skill-draft-editor-badge" data-tone="accent">${escapeHtml(skillDraftT('skill_draft_manifest_badge', 'Manifest'))}</span>`
            : `<span class="skill-draft-editor-badge">${escapeHtml(item.kind === 'source_file'
                ? skillDraftT('skill_draft_file_linked', 'Linked')
                : skillDraftT('skill_draft_file_editable', 'Editable'))}</span>`;
        const path = item.id === 'skill-md' ? 'SKILL.md' : `${item.folder_type || '…'}/${item.filename}`;
        const removeLabel = skillDraftTf('skill_draft_remove_named_file_aria', 'Remove {filename}', { filename: item.filename });
        const remove = item.locked ? '' : `
            <button class="skill-draft-editor-remove om-button" data-remove-file="${escapeHtml(item.id)}" type="button" aria-label="${escapeHtml(removeLabel)}" title="${escapeHtml(skillDraftT('skill_draft_remove_file', 'Remove'))}"${state.savedSkillId ? ' disabled' : ''}>
                ${getIcon('trash')}
            </button>`;
        return `
            <div class="skill-draft-editor-head">
                <div class="skill-draft-editor-head-left">${badge}<span class="skill-draft-editor-path">${escapeHtml(path)}</span></div>
                ${remove}
            </div>`;
    }

    function buildManifestPreviewMarkup(item) {
        const parsed = parseSkillMarkdown(item.content);
        const rows = [
            [skillDraftT('skill_draft_preview_name', 'Name'), parsed.name ? humanizeSkillName(parsed.name) : '—'],
            [skillDraftT('skill_draft_preview_description', 'Description'), parsed.description || '—'],
            [skillDraftT('skill_draft_preview_compatibility', 'Compatibility'), parsed.compatibility || '—'],
            [skillDraftT('skill_draft_preview_license', 'License'), parsed.license || '—'],
        ].map(([label, value]) => `
            <div class="skill-draft-frontmatter-row">
                <span class="skill-draft-frontmatter-key">${escapeHtml(label)}</span>
                <span class="skill-draft-frontmatter-value">${escapeHtml(value)}</span>
            </div>`).join('');
        return `
            <div class="skill-draft-frontmatter-card">${rows}</div>
            <div class="skill-draft-markdown-preview canvas-markdown-render markdown-body" data-role="manifest-preview"></div>`;
    }

    function buildEditorBodyMarkup(item, state) {
        const disabled = state.savedSkillId ? ' disabled' : '';
        if (item.id === 'skill-md' && state.view === 'preview') {
            return buildManifestPreviewMarkup(item);
        }

        if (item.kind === 'source_file') {
            const previewUrl = safeImageUrl(item.preview_url);
            const preview = previewUrl
                ? `<img src="${escapeHtml(previewUrl)}" alt="${escapeHtml(item.filename)}">`
                : `${getIcon('image')}<span>${escapeHtml(skillDraftT('skill_draft_no_preview', 'No preview available'))}</span>`;
            return `
                <div class="skill-draft-linked-card">
                    <div class="skill-draft-linked-preview">${preview}</div>
                    <div class="skill-draft-linked-grid">
                        <div><span>${escapeHtml(skillDraftT('skill_draft_label_source', 'Source'))}</span><strong>${escapeHtml(item.source_file_name || item.source_file_id || skillDraftT('skill_draft_generated_file', 'Generated file'))}</strong></div>
                        <div><span>${escapeHtml(skillDraftT('skill_draft_label_type', 'Type'))}</span><strong>${escapeHtml(item.media_type || item.source_file_category || 'file')}</strong></div>
                        <div><span>${escapeHtml(skillDraftT('skill_draft_label_size', 'Size'))}</span><strong>${escapeHtml(formatBytes(item.source_file_size || 0))}</strong></div>
                    </div>
                    <p>${escapeHtml(skillDraftT('skill_draft_linked_file_copy_note', 'This file will be copied from an existing generated file when you save the skill.'))}</p>
                </div>`;
        }

        const fields = item.id === 'skill-md' ? '' : `
            <div class="skill-draft-editor-fields">
                <label class="skill-draft-editor-field">
                    <span>${escapeHtml(skillDraftT('skill_draft_field_filename', 'Filename'))}</span>
                    <input type="text" class="skill-draft-editor-input skills-form-input" data-field="filename" value="${escapeHtml(item.filename)}" spellcheck="false"${disabled}>
                </label>
                <label class="skill-draft-editor-field">
                    <span>${escapeHtml(skillDraftT('skill_draft_field_folder', 'Folder'))}</span>
                    <span class="skill-draft-editor-select-wrap">
                        <select class="skill-draft-editor-select skills-form-input" data-field="folder_type"${disabled}>
                            ${FOLDER_ORDER.map((folder) => `<option value="${folder}"${folder === item.folder_type ? ' selected' : ''}>${escapeHtml(getFolderLabel(folder))}</option>`).join('')}
                        </select>
                        <span aria-hidden="true">${getIcon('chevron')}</span>
                    </span>
                </label>
            </div>`;
        const placeholder = skillDraftT('skill_draft_write_file_placeholder', 'Write the file content…');
        return `${fields}<textarea class="skill-draft-editor-textarea skills-form-textarea" data-field="content" spellcheck="false" placeholder="${escapeHtml(placeholder)}"${disabled}>${escapeHtml(item.content || '')}</textarea>`;
    }

    function autosizeEditor(textarea) {
        if (!textarea) return;
        textarea.style.height = 'auto';
        textarea.style.height = `${Math.max(340, textarea.scrollHeight + 2)}px`;
    }

    function renderManifestMarkdown(target, markdown) {
        if (!target) return;
        const content = stripFrontmatter(markdown);
        if (typeof window.renderMarkdownContent === 'function') {
            window.renderMarkdownContent(target, content);
            return;
        }
        target.textContent = content;
    }

    function renderEditor(state) {
        if (!panelEditor) return;
        const item = getSelectedItem(state);
        if (!item) {
            panelEditor.textContent = '';
            return;
        }
        panelEditor.innerHTML = buildEditorHeadMarkup(item, state) + buildEditorBodyMarkup(item, state);
        autosizeEditor(panelEditor.querySelector('.skill-draft-editor-textarea'));
        if (item.id === 'skill-md' && state.view === 'preview') {
            renderManifestMarkdown(panelEditor.querySelector('[data-role="manifest-preview"]'), item.content);
        }
    }

    function renderFooter(state) {
        if (panelFooterStatus && panelFooterStatusText) {
            const statusClass = {
                info: '',
                dirty: 'unsaved',
                success: 'complete',
                error: 'error',
            }[state.statusKind || 'info'] || '';
            panelFooterStatus.className = `skill-draft-footer-status canvas-markdown-preview-status ${statusClass}`.trim();
            panelFooterStatusText.textContent = state.statusText;
        }
        if (panelSave) {
            panelSave.disabled = Boolean(state.saving || state.savedSkillId);
            if (state.saving) {
                panelSave.textContent = skillDraftT('skill_draft_saving', 'Saving...');
            } else if (state.savedSkillId) {
                panelSave.innerHTML = `${getIcon('check')}<span>${escapeHtml(skillDraftT('skill_draft_saved', 'Saved'))}</span>`;
            } else {
                panelSave.textContent = skillDraftT('skill_draft_save_to_workspace', 'Save to workspace');
            }
        }
        if (panelOpenWorkspace) panelOpenWorkspace.hidden = !state.savedSkillId;
    }

    function renderPanel() {
        const state = activeCard?.__skillDraftState;
        if (!state) return;
        renderHeader(state);
        renderFiles(state);
        renderEditor(state);
        renderFooter(state);
        renderAllCards();
    }

    function setStatus(state, kind, text, { render = true } = {}) {
        state.statusKind = kind;
        state.statusText = text;
        if (render && activeCard?.__skillDraftState === state) renderPanel();
    }

    function markDirty(state) {
        if (state.savedSkillId) return;
        state.dirty = true;
        renderHeader(state);
        renderFooter(state);
        renderCard(activeCard);
    }

    function addNewFile() {
        const state = activeCard?.__skillDraftState;
        if (!state || state.savedSkillId) return;
        let suffix = 1;
        let candidate = 'new-file.md';
        const existing = new Set(
            state.items
                .filter((item) => item.folder_type === 'references')
                .map((item) => item.filename.toLowerCase()),
        );
        while (existing.has(candidate.toLowerCase())) {
            suffix += 1;
            candidate = `new-file-${suffix}.md`;
        }
        const item = {
            id: createInlineFileId('new'),
            filename: candidate,
            folder_type: 'references',
            kind: 'inline_text',
            content: '',
            media_type: 'text/markdown',
            description: '',
        };
        state.items.push(item);
        state.selectedId = item.id;
        state.view = 'edit';
        state.dirty = true;
        setStatus(state, 'info', skillDraftT('skill_draft_file_added', 'Added a new editable file to the draft.'));
        const input = panelEditor?.querySelector('[data-field="filename"]');
        input?.focus();
        input?.select();
    }

    function removeFile(fileId) {
        const state = activeCard?.__skillDraftState;
        if (!state || state.savedSkillId) return;
        const item = state.items.find((candidate) => candidate.id === fileId);
        if (!item || item.locked) return;
        state.items = state.items.filter((candidate) => candidate.id !== fileId);
        if (state.selectedId === fileId) state.selectedId = 'skill-md';
        state.dirty = true;
        setStatus(state, 'info', skillDraftTf('skill_draft_named_file_removed', 'Removed {filename} from the draft.', {
            filename: item.filename,
        }));
    }

    function buildSavePayload(state) {
        return {
            skill_markdown: state.items.find((item) => item.id === 'skill-md')?.content || '',
            icon: state.icon || undefined,
            files: state.items.filter((item) => item.id !== 'skill-md').map((item) => {
                if (item.kind === 'source_file') {
                    return {
                        folder_type: item.folder_type,
                        filename: item.filename,
                        source_file_id: item.source_file_id,
                        media_type: item.media_type || undefined,
                        description: item.description || undefined,
                    };
                }
                return {
                    folder_type: item.folder_type,
                    filename: item.filename,
                    content: item.content || '',
                    encoding: 'utf-8',
                    media_type: item.media_type || undefined,
                    description: item.description || undefined,
                };
            }),
        };
    }

    /** Persist the reviewed draft through the authenticated, audited backend route. */
    async function saveDraft() {
        const state = activeCard?.__skillDraftState;
        if (!state || state.saving || state.savedSkillId) return;
        const errors = collectValidationErrors(state);
        if (errors.length) {
            setStatus(state, 'error', errors[0]);
            return;
        }

        state.saving = true;
        renderFooter(state);
        try {
            const request = typeof window.authedFetch === 'function' ? window.authedFetch : window.fetch;
            const response = await request('/api/v1/skills/draft/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(buildSavePayload(state)),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                // Draft persistence returns safe validation details such as a
                // missing source file or an exceeded size/count limit. Preserve
                // those actionable details instead of collapsing every failure
                // into a message that gives the user nothing to correct.
                const detail = typeof payload?.detail === 'string' ? payload.detail.trim() : '';
                throw new Error(detail || skillDraftT('skill_draft_save_failed_period', 'Failed to save skill draft.'));
            }

            state.savedSkillId = String(payload.skill_id || '');
            state.savedTitle = String(payload.title || '');
            state.dirty = false;
            state.statusKind = 'success';
            state.statusText = skillDraftT('skill_draft_added_to_workspace', 'Skill added to your workspace.');
            if (typeof window.showNotification === 'function') {
                window.showNotification(state.statusText, 'success');
            }
            window.dispatchEvent(new CustomEvent('workspaceSkills:changed', {
                detail: { reason: 'draft-saved', skillId: state.savedSkillId },
            }));
            if (window.SkillsManager?.loadSkills && window.location.pathname === '/workspace/skills') {
                void window.SkillsManager.loadSkills();
            }
        } catch (error) {
            state.statusKind = 'error';
            state.statusText = error?.message || skillDraftT('skill_draft_save_failed_period', 'Failed to save skill draft.');
            if (typeof window.showNotification === 'function') {
                window.showNotification(state.statusText, 'error');
            }
        } finally {
            state.saving = false;
            renderPanel();
        }
    }

    function openWorkspaceSkills() {
        closeSidebar({ restoreFocus: false });
        if (typeof window.navigateTo === 'function') {
            window.navigateTo('/workspace/skills');
            return;
        }
        window.location.href = '/workspace/skills';
    }

    function canvasSizingController() {
        return window.canvasMarkdownWidget || null;
    }

    function updateResizerA11y() {
        if (!panelResizer) return;
        const controller = canvasSizingController();
        const bounds = controller?.getPreviewWidthBounds?.();
        const ratio = Number(controller?.getPreviewWidthRatio?.() || 0.5);
        if (!bounds?.viewportWidth) return;
        panelResizer.setAttribute('aria-valuemin', String(Math.round((bounds.minWidth / bounds.viewportWidth) * 100)));
        panelResizer.setAttribute('aria-valuemax', String(Math.round((bounds.maxWidth / bounds.viewportWidth) * 100)));
        panelResizer.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    }

    function stopResize(event) {
        if (!resizeActive) return;
        resizeActive = false;
        document.body.classList.remove('skill-draft-preview-resizing');
        if (resizePointerId !== null) {
            panelResizer?.releasePointerCapture?.(resizePointerId);
        }
        resizePointerId = null;
        if (event) updateResizerA11y();
    }

    function setWidthFromPointer(clientX, { persist = false } = {}) {
        const controller = canvasSizingController();
        controller?.setPreviewWidthFromPointerX?.(clientX, { persist });
        updateResizerA11y();
    }

    function handleResizerPointerDown(event) {
        if (window.innerWidth <= DESKTOP_BREAKPOINT) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        event.preventDefault();
        resizeActive = true;
        resizePointerId = event.pointerId;
        document.body.classList.add('skill-draft-preview-resizing');
        panelResizer?.setPointerCapture?.(event.pointerId);
        setWidthFromPointer(event.clientX);
    }

    function handleResizerPointerMove(event) {
        if (!resizeActive) return;
        event.preventDefault();
        setWidthFromPointer(event.clientX);
    }

    function handleResizerKeydown(event) {
        if (window.innerWidth <= DESKTOP_BREAKPOINT) return;
        const controller = canvasSizingController();
        const bounds = controller?.getPreviewWidthBounds?.();
        if (!bounds?.viewportWidth) return;
        const currentWidth = bounds.viewportWidth * Number(controller.getPreviewWidthRatio?.() || 0.5);
        const step = event.shiftKey ? RESIZE_LARGE_STEP : RESIZE_STEP;
        let nextWidth = null;
        if (event.key === 'ArrowLeft') nextWidth = currentWidth + step;
        else if (event.key === 'ArrowRight') nextWidth = currentWidth - step;
        else if (event.key === 'Home') nextWidth = bounds.minWidth;
        else if (event.key === 'End') nextWidth = bounds.maxWidth;
        else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            controller.resetPreviewWidth?.();
            updateResizerA11y();
            return;
        }
        if (nextWidth === null) return;
        event.preventDefault();
        controller.setPreviewWidthFromPixels?.(nextWidth, { persist: true });
        updateResizerA11y();
    }

    /** Open one draft and hand off the shared artifact-preview space atomically. */
    function openEditor(card, { focus = true } = {}) {
        if (!panel || !card?.__skillDraftState) return;
        activeCard = card;
        activeTrigger = card.querySelector('[data-action="open-editor"]');
        previewVisible = true;
        canvasSizingController()?.applyPreviewWidthRatio?.();
        updateResizerA11y();
        renderPanel();

        panel.classList.add('visible');
        panel.setAttribute('aria-hidden', 'false');
        panel.removeAttribute('inert');
        document.body.classList.add('skill-draft-preview-open');
        if (typeof window.setMainSidebarAutoCollapsed === 'function') {
            window.setMainSidebarAutoCollapsed('skill-draft-preview', true);
        } else if (typeof window.closeSidebar === 'function') {
            window.closeSidebar({ persist: false });
        }

        window.closeOtherArtifactPreviews?.('skill-draft-preview');
        renderAllCards();
        if (focus) window.requestAnimationFrame?.(() => panelClose?.focus());
    }

    function closeSidebar({ restoreFocus = true } = {}) {
        if (!previewVisible) return;
        const focusTarget = activeTrigger;
        previewVisible = false;
        stopResize();
        panel?.classList.remove('visible');
        panel?.setAttribute('aria-hidden', 'true');
        panel?.setAttribute('inert', '');
        document.body.classList.remove('skill-draft-preview-open');
        window.setMainSidebarAutoCollapsed?.('skill-draft-preview', false);
        renderAllCards();
        if (restoreFocus && focusTarget?.isConnected) focusTarget.focus();
    }

    function handleClick(event) {
        const openButton = event.target.closest?.(`${CARD_SELECTOR} [data-action="open-editor"]`);
        if (openButton) {
            const card = openButton.closest(CARD_SELECTOR);
            if (previewVisible && activeCard === card) closeSidebar();
            else openEditor(card);
            return;
        }

        if (!panel?.contains(event.target)) return;
        if (event.target.closest('#skillDraftPreviewClose')) {
            closeSidebar();
            return;
        }
        if (event.target.closest('#skillDraftAddFile')) {
            addNewFile();
            return;
        }
        if (event.target.closest('#skillDraftSave')) {
            void saveDraft();
            return;
        }
        if (event.target.closest('#skillDraftOpenWorkspace')) {
            openWorkspaceSkills();
            return;
        }

        const viewButton = event.target.closest('[data-skill-draft-view]');
        if (viewButton && activeCard?.__skillDraftState) {
            activeCard.__skillDraftState.view = viewButton.dataset.skillDraftView;
            renderHeader(activeCard.__skillDraftState);
            renderEditor(activeCard.__skillDraftState);
            return;
        }
        const selectButton = event.target.closest('[data-select-file]');
        if (selectButton && activeCard?.__skillDraftState) {
            activeCard.__skillDraftState.selectedId = selectButton.dataset.selectFile;
            activeCard.__skillDraftState.view = 'edit';
            renderPanel();
            return;
        }
        const removeButton = event.target.closest('[data-remove-file]');
        if (removeButton) removeFile(removeButton.dataset.removeFile);
    }

    function handleInput(event) {
        if (!panel?.contains(event.target)) return;
        const state = activeCard?.__skillDraftState;
        const item = state ? getSelectedItem(state) : null;
        const field = event.target.getAttribute?.('data-field');
        if (!state || !item || !field || state.savedSkillId) return;

        if (field === 'content') {
            item.content = event.target.value;
            autosizeEditor(event.target);
            if (item.id === 'skill-md') {
                const parsed = parseSkillMarkdown(item.content);
                state.statusKind = parsed.error ? 'error' : 'info';
                state.statusText = parsed.error || skillDraftT('skill_draft_manifest_updated', 'SKILL.md updated. You can keep editing before saving.');
            }
            markDirty(state);
        } else if (field === 'filename') {
            item.filename = event.target.value;
            markDirty(state);
        }
    }

    function handleChange(event) {
        if (!panel?.contains(event.target)) return;
        const state = activeCard?.__skillDraftState;
        const item = state ? getSelectedItem(state) : null;
        const field = event.target.getAttribute?.('data-field');
        if (!state || !item || state.savedSkillId || !['filename', 'folder_type'].includes(field)) return;
        if (field === 'filename') item.filename = event.target.value.trim();
        if (field === 'folder_type') item.folder_type = event.target.value;
        state.dirty = true;
        renderPanel();
        panelEditor?.querySelector(`[data-field="${field}"]`)?.focus();
    }

    function handleKeydown(event) {
        const viewButton = event.target.closest?.('[data-skill-draft-view]');
        if (viewButton && panel?.contains(viewButton)) {
            const buttons = Array.from(panelViewToggle?.querySelectorAll('[data-skill-draft-view]') || []);
            const currentIndex = buttons.indexOf(viewButton);
            let nextIndex = -1;
            if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + buttons.length) % buttons.length;
            else if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % buttons.length;
            else if (event.key === 'Home') nextIndex = 0;
            else if (event.key === 'End') nextIndex = buttons.length - 1;
            if (nextIndex >= 0 && buttons[nextIndex]) {
                event.preventDefault();
                buttons[nextIndex].focus();
                buttons[nextIndex].click();
                return;
            }
        }
        if (event.key === 'Escape' && previewVisible) {
            event.preventDefault();
            closeSidebar();
        }
    }

    function initStaticIcons() {
        document.querySelectorAll('[data-skill-draft-icon]').forEach((element) => {
            element.innerHTML = getIcon(element.dataset.skillDraftIcon);
        });
    }

    function initWidget(card, { autoOpen = false } = {}) {
        if (!card || card.dataset.skillDraftInit === 'true') return;
        try {
            card.__skillDraftState = buildInitialState(parseDraftPayload(card));
            card.dataset.skillDraftInit = 'true';
            renderCard(card);
            if (autoOpen) openEditor(card);
        } catch (error) {
            card.dataset.skillDraftInit = 'error';
            card.setAttribute('role', 'status');
            card.textContent = error?.message || skillDraftT('skill_draft_load_failed', 'Failed to load skill draft.');
        }
    }

    function initWidgets(root = document, options = {}) {
        if (root.matches?.(CARD_SELECTOR)) initWidget(root, options);
        root.querySelectorAll?.(`${CARD_SELECTOR}:not([data-skill-draft-init="true"])`).forEach((card) => initWidget(card, options));
    }

    const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            mutation.addedNodes.forEach((node) => {
                if (!(node instanceof HTMLElement)) return;
                initWidgets(node);
            });
        }
        if (previewVisible && activeCard && !activeCard.isConnected) closeSidebar({ restoreFocus: false });
    });

    function startObserver() {
        if (!document.body) return;
        initStaticIcons();
        initWidgets(document);
        observer.observe(document.body, { childList: true, subtree: true });
    }

    document.addEventListener('click', handleClick);
    document.addEventListener('input', handleInput);
    document.addEventListener('change', handleChange);
    document.addEventListener('keydown', handleKeydown);
    document.addEventListener('i18n:updated', () => {
        renderAllCards();
        if (previewVisible) renderPanel();
    });
    panelResizer?.addEventListener('pointerdown', handleResizerPointerDown);
    panelResizer?.addEventListener('pointermove', handleResizerPointerMove);
    panelResizer?.addEventListener('pointerup', stopResize);
    panelResizer?.addEventListener('pointercancel', stopResize);
    panelResizer?.addEventListener('keydown', handleResizerKeydown);
    panelResizer?.addEventListener('dblclick', () => {
        canvasSizingController()?.resetPreviewWidth?.();
        updateResizerA11y();
    });
    window.addEventListener('resize', updateResizerA11y);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startObserver, { once: true });
    } else {
        startObserver();
    }

    window.skillDraftWidget = {
        initWidget,
        initWidgets,
        openEditor,
        closeSidebar,
        isOpen: () => previewVisible,
    };
})();
