(function () {
    const desc = (id, i18n, text, attrs) => ({ id, i18n, text, attrs });
    const htmlDesc = (html, attrs) => ({ html, attrs });
    const title = (id, i18n, text) => ({ id, i18n, text });
    const cancel = (id, i18n = 'common_cancel', text = 'Cancel') => ({ id, role: 'cancel', variant: 'cancel', i18n, text });
    const danger = (id, i18n, text, textId, options = {}) => ({ id, variant: 'danger', i18n, text, textId, ...options });
    const submit = (id, i18n, text, textId, options = {}) => ({ id, variant: 'submit', i18n, text, textId, ...options });
    const closeIcon = () => window.Icons?.close || '<span aria-hidden="true">x</span>';
    const plusIcon = () => window.Icons?.plus || '<span aria-hidden="true">+</span>';
    const fileIcon = () => window.Icons?.file || '';
    const infoIcon = () => window.Icons?.info || '';
    const uploadIcon = () => window.Icons?.upload || fileIcon();
    // The icon registry normally provides both SVGs. Text-symbol fallbacks keep
    // the compact, icon-only tabs usable if that registry loads unexpectedly late.
    const microphoneIcon = () => window.Icons?.microphone || '<span aria-hidden="true">●</span>';
    const screenAudioIcon = () => window.Icons?.chatFilesScreenCapture || '<span aria-hidden="true">▣</span>';
    const linkIcon = Icons.withSvgAttributes("admin_sidebar_connections", { "width": "22", "height": "22" });

    const sharedShell = ({ id, overlayClass, cardClass, cardId, labelledby, describedby, bodyHtml, actions = [], actionsLeadHtml = '', hidden = true, overlayAttrs = {} }) => ({
        id,
        hidden,
        overlayClass,
        overlayAttrs,
        cardId,
        cardClass: ['shared-modal-card', cardClass].filter(Boolean).join(' '),
        role: 'dialog',
        ariaModal: 'true',
        ariaLabelledby: labelledby,
        ariaDescribedby: describedby,
        contentHtml: bodyHtml,
        actions,
        actionsLeadHtml,
    });

    const sharedAction = (id, i18n, text, variant = 'submit', options = {}) => ({ id, variant, i18n, text, ...options });

    const closeButton = (id, className, i18n = 'common_close', label = 'Close') => `
        <button type="button" class="${className} shared-modal-close" id="${id}" aria-label="${label}" data-i18n-attr="aria-label:${i18n}">
            ${closeIcon()}
        </button>
    `;

    const skillAcceptModal = () => sharedShell({
        id: 'skillAcceptOverlay',
        overlayClass: 'skill-accept-overlay',
        cardClass: 'skill-accept-modal',
        labelledby: 'skillAcceptTitle',
        bodyHtml: `
            <header class="skill-accept-modal-header shared-modal-header shared-modal-header--main">
                <div class="skill-accept-icon" id="skillAcceptIcon">
                    ${Icons.resolveIcon("lightning")}
                </div>
                <div class="skill-accept-header-text">
                    <p class="skill-accept-badge" data-i18n="workspace_skills_accept_badge">Shared Skill</p>
                    <h3 class="skill-accept-title shared-modal-title" id="skillAcceptTitle" data-i18n="workspace_skills_accept_loading">Loading...</h3>
                    <p class="skill-accept-description" id="skillAcceptOwner"></p>
                </div>
            </header>
            <div class="skill-accept-modal-body shared-modal-body">
                <div class="share-accept-type-info" id="skillAcceptShareTypeInfo"></div>
                <div class="skill-accept-preview-section">
                    <label class="skill-accept-preview-label" data-i18n="workspace_skills_accept_preview_label">Skill Preview</label>
                    <div class="skill-accept-preview" id="skillAcceptPreviewContent"></div>
                </div>
            </div>
        `,
        actions: [cancel('skillAcceptCancelBtn'), sharedAction('skillAcceptConfirmBtn', 'workspace_skills_accept_add', 'Add to My Skills', 'submit', { html: `${plusIcon()}<span id="skillAcceptConfirmText" data-i18n="workspace_skills_accept_add">Add to My Skills</span>` })],
    });

    const shareAcceptModal = ({ id, titleId, ownerId, previewId, previewContentId, confirmId, badgeI18n, badgeText, titleI18n, titleText, previewI18n, previewText, confirmI18n, confirmText, iconId, iconStyle, iconSvg, typeInfoId, descHtml = '', describedby, overlayAttrs = {} }) => sharedShell({
        id,
        overlayClass: 'share-accept-overlay',
        cardClass: 'share-accept-modal',
        labelledby: titleId,
        describedby,
        overlayAttrs,
        bodyHtml: `
            <header class="share-accept-modal-header shared-modal-header shared-modal-header--main">
                <div class="share-accept-icon" id="${iconId}" style="${iconStyle}">
                    ${iconSvg}
                </div>
                <div class="share-accept-header-text">
                    <p class="share-accept-badge" data-i18n="${badgeI18n}">${badgeText}</p>
                    <h3 class="share-accept-title shared-modal-title" id="${titleId}" data-i18n="${titleI18n}">${titleText}</h3>
                    <p class="share-accept-owner" id="${ownerId}"></p>
                </div>
            </header>
            <div class="share-accept-modal-body shared-modal-body">
                ${descHtml}
                ${typeInfoId ? `<div class="share-accept-type-info" id="${typeInfoId}"></div>` : ''}
                <div class="share-accept-preview" id="${previewId}">
                    <p class="share-accept-preview-label" data-i18n="${previewI18n}">${previewText}</p>
                    <div class="share-accept-preview-content" id="${previewContentId}"></div>
                </div>
            </div>
        `,
        actions: [cancel(id.replace('Overlay', 'CancelBtn')), sharedAction(confirmId, confirmI18n, confirmText, 'submit', { html: `${plusIcon()}<span data-i18n="${confirmI18n}">${confirmText}</span>` })],
    });

    const skillImportModal = () => sharedShell({
        id: 'skillImportOverlay',
        overlayClass: 'skill-import-overlay',
        cardClass: 'skill-import-modal shared-modal--wide',
        labelledby: 'skillImportModalTitle',
        bodyHtml: `
            <header class="skill-import-modal-header shared-modal-header shared-modal-header--main">
                <div class="skill-import-modal-title-row">
                    <div class="skill-import-modal-icon">${fileIcon()}</div>
                    <div>
                        <h3 class="skill-import-title shared-modal-title" id="skillImportModalTitle" data-i18n="workspace_skills_import_modal_title">Import Skill</h3>
                        <p class="skill-import-subtitle shared-modal-subtitle" data-i18n="workspace_skills_import_modal_subtitle">Import one or more .md files, or paste markdown directly</p>
                    </div>
                </div>
                ${closeButton('skillImportCloseBtn', 'om-button')}
            </header>
            <div class="skill-import-modal-body shared-modal-body">
                <div class="skill-import-tabs" role="tablist">
                    <button type="button" class="skill-import-tab active" id="skillImportTabFile" role="tab" aria-selected="true" aria-controls="skillImportPanelFile">
                        ${fileIcon()}<span data-i18n="workspace_skills_import_tab_file">Upload Files</span>
                    </button>
                    <button type="button" class="skill-import-tab" id="skillImportTabPaste" role="tab" aria-selected="false" aria-controls="skillImportPanelPaste">
                        ${fileIcon()}<span data-i18n="workspace_skills_import_tab_paste">Paste Markdown</span>
                    </button>
                </div>
                <div class="skill-import-panel" id="skillImportPanelFile" role="tabpanel">
                    <div class="skill-import-dropzone" id="skillImportDropzone">
                        <input type="file" id="skillImportFileInput" accept=".md,text/markdown" multiple hidden>
                        <div class="skill-import-dropzone-content" id="skillImportDropzoneContent">
                            <div class="skill-import-dropzone-icon">${fileIcon()}</div>
                            <p class="skill-import-dropzone-title" data-i18n="workspace_skills_import_dropzone_title">Drop your .md files here</p>
                            <p class="skill-import-dropzone-hint"><span data-i18n="workspace_skills_import_dropzone_hint_prefix">or</span> <button type="button" class="om-button border cancel" id="skillImportBrowseBtn" data-i18n="workspace_skills_import_browse">browse files</button></p>
                            <p class="skill-import-dropzone-formats" data-i18n="workspace_skills_import_dropzone_formats">Accepts .md files with valid SKILL.md frontmatter</p>
                        </div>
                        <div class="skill-import-file-selected" id="skillImportFileSelected" hidden>
                            <div class="skill-import-file-selected-header">
                                <div class="skill-import-file-icon">${fileIcon()}</div>
                                <div class="skill-import-file-info">
                                    <p class="skill-import-file-name" id="skillImportFileName"></p>
                                    <p class="skill-import-file-size" id="skillImportFileSize"></p>
                                </div>
                                <button type="button" class="skill-import-file-remove" id="skillImportFileRemove" data-i18n-attr="aria-label:workspace_skills_import_remove_file_aria" aria-label="Clear selected files">${closeIcon()}</button>
                            </div>
                            <div class="skill-import-file-list" id="skillImportFileList" role="list" aria-live="polite"></div>
                        </div>
                    </div>
                </div>
                <div class="skill-import-panel" id="skillImportPanelPaste" role="tabpanel" hidden>
                    <div class="skill-import-paste-container">
                        <div class="skill-import-paste-header">
                            <label for="skillImportPasteInput" class="skill-import-paste-label" data-i18n="workspace_skills_import_paste_label">Paste your SKILL.md content</label>
                            <button type="button" class="skill-import-paste-clear" id="skillImportPasteClear" hidden data-i18n="workspace_skills_import_paste_clear">${closeIcon()}Clear</button>
                        </div>
                        <textarea id="skillImportPasteInput" class="skill-import-paste-textarea" data-i18n-attr="placeholder:workspace_skills_import_paste_placeholder" placeholder="---&#10;name: my-skill-name&#10;description: A brief description of what this skill does.&#10;---&#10;&#10;# My Skill&#10;&#10;## When to use this skill&#10;..." spellcheck="false" autocomplete="off"></textarea>
                    </div>
                </div>
                <div class="skill-import-feedback" id="skillImportFeedback" hidden>
                    <div class="skill-import-error" id="skillImportError" hidden>
                        <div class="skill-import-error-icon">${fileIcon()}</div>
                        <div class="skill-import-error-content">
                            <p class="skill-import-error-title" data-i18n="workspace_skills_import_error_title">Invalid Skill Markdown</p>
                            <p class="skill-import-error-message" id="skillImportErrorMessage"></p>
                        </div>
                    </div>
                    <div class="skill-import-preview" id="skillImportPreview" hidden>
                        <div class="skill-import-preview-header">
                            <div class="skill-import-preview-valid-badge" data-i18n="workspace_skills_import_valid_badge">Valid skill</div>
                            <p class="skill-import-preview-label" data-i18n="workspace_skills_import_preview_label">Preview</p>
                        </div>
                        <div class="skill-import-preview-card">
                            <div class="skill-import-preview-icon" id="skillImportPreviewIcon">${fileIcon()}</div>
                            <div class="skill-import-preview-info">
                                <p class="skill-import-preview-name" id="skillImportPreviewName"></p>
                                <p class="skill-import-preview-description" id="skillImportPreviewDescription"></p>
                            </div>
                        </div>
                        <div class="skill-import-preview-meta" id="skillImportPreviewMeta"></div>
                        <div class="skill-import-preview-body" id="skillImportPreviewBody" hidden>
                            <p class="skill-import-preview-body-label" data-i18n="workspace_skills_import_instructions_preview_label">Instructions preview</p>
                            <div class="skill-import-preview-body-text" id="skillImportPreviewBodyText"></div>
                        </div>
                    </div>
                </div>
            </div>
        `,
        actions: [
            cancel('skillImportCancelBtn'),
            submit('skillImportConfirmBtn', 'workspace_skills_import_confirm', 'Import Skill', 'skillImportConfirmText', { disabled: true, html: `${fileIcon()}<span id="skillImportConfirmText" data-i18n="workspace_skills_import_confirm">Import Skill</span>` }),
        ],
    });

    const notesFilePickerModal = () => sharedShell({
        id: 'notesFilePickerOverlay',
        overlayClass: 'notes-file-picker-overlay',
        cardClass: 'notes-file-picker-modal shared-modal--wide',
        labelledby: 'notesFilePickerTitle',
        bodyHtml: `
            <header class="notes-file-picker-header shared-modal-header shared-modal-header--main">
                <div>
                    <h3 class="shared-modal-title" id="notesFilePickerTitle" data-i18n="notes_file_picker_title">Choose Files</h3>
                    <p class="notes-file-picker-subtitle shared-modal-subtitle" id="notesFilePickerSubtitle" data-i18n="notes_file_picker_subtitle">Select uploaded files or upload new ones for this note.</p>
                </div>
                ${closeButton('notesFilePickerCloseBtn', 'notes-file-picker-close')}
            </header>
            <div class="notes-file-picker-body shared-modal-body">
                <div class="notes-file-picker-toolbar">
                    <input type="search" id="notesFilePickerSearch" placeholder="Search uploaded files" aria-label="Search uploaded files" data-i18n-attr="placeholder:notes_file_picker_search_placeholder;aria-label:notes_file_picker_search_aria">
                    <button type="button" class="notes-file-picker-upload-btn" id="notesFilePickerUploadBtn">${fileIcon()}<span data-i18n="header_upload">Upload</span></button>
                    <input type="file" id="notesFilePickerUploadInput" multiple hidden>
                </div>
                <div class="notes-file-picker-filters">
                    <button type="button" class="notes-file-picker-filter active" data-filter="all" data-i18n="notes_filter_all">All</button>
                    <button type="button" class="notes-file-picker-filter" data-filter="document" data-i18n="notes_filter_documents">Documents</button>
                    <button type="button" class="notes-file-picker-filter" data-filter="image" data-i18n="notes_filter_images">Images</button>
                    <button type="button" class="notes-file-picker-filter" data-filter="audio" data-i18n="notes_filter_audio">Audio</button>
                </div>
                <div class="notes-file-picker-status" id="notesFilePickerStatus" data-i18n="notes_file_picker_loading">Loading uploaded files...</div>
                <div class="notes-file-picker-list" id="notesFilePickerList"></div>
                <div class="notes-file-picker-empty" id="notesFilePickerEmpty" hidden data-i18n="notes_file_picker_empty">No matching files found.</div>
            </div>
        `,
        actions: [cancel('notesFilePickerCancelBtn'), submit('notesFilePickerConfirmBtn', 'notes_file_picker_insert_selected', 'Insert Selected', null, { disabled: true })],
    });

    const notesRecordingModal = () => sharedShell({
        id: 'notesRecordingOverlay',
        overlayClass: 'notes-recording-overlay',
        cardClass: 'notes-recording-modal shared-modal--wide',
        labelledby: 'notesRecordingTitle',
        bodyHtml: `
            <header class="notes-recording-header shared-modal-header shared-modal-header--main">
                <div>
                    <h3 class="shared-modal-title" id="notesRecordingTitle" data-i18n="notes_record_audio">Record Audio</h3>
                    <p class="notes-recording-subtitle shared-modal-subtitle" data-i18n="notes_recording_subtitle">Capture audio and add it inline to this note as a playable attachment.</p>
                </div>
                ${closeButton('notesRecordingCloseBtn', 'notes-recording-close')}
            </header>
            <div class="notes-recording-body shared-modal-body">
                <div class="notes-recording-source-switch" role="tablist" aria-label="Recording source" data-i18n-attr="aria-label:notes_recording_source_aria">
                    <button type="button" class="notes-recording-source-btn active" id="notesRecordingSourceMicrophone" data-source="microphone" aria-pressed="true">${fileIcon()}<span data-i18n="notes_recording_source_microphone">Microphone</span></button>
                    <button type="button" class="notes-recording-source-btn" id="notesRecordingSourceScreen" data-source="screen" aria-pressed="false">${fileIcon()}<span data-i18n="notes_recording_source_screen_audio">Screen Audio</span></button>
                </div>
                <div class="notes-recording-status-card">
                    <div>
                        <p class="notes-recording-status-label" id="notesRecordingStatus" data-i18n="notes_recording_status_ready">Ready to record</p>
                        <p class="notes-recording-status-details" id="notesRecordingDetails" data-i18n="notes_recording_details_ready">Use your microphone or capture shared tab audio for a meeting, then add the audio inline to this note.</p>
                    </div>
                    <div class="notes-recording-timer" id="notesRecordingTimer">00:00</div>
                </div>
                <div class="notes-recording-preview" id="notesRecordingPreview" hidden>
                    <div class="notes-recording-preview-copy">
                        <p class="notes-recording-preview-name" id="notesRecordingPreviewName"></p>
                        <p class="notes-recording-preview-meta" id="notesRecordingPreviewMeta"></p>
                    </div>
                    <audio id="notesRecordingPreviewAudio" class="notes-recording-preview-audio" controls preload="metadata"></audio>
                </div>
            </div>
        `,
        actions: [
            cancel('notesRecordingCancelBtn'),
            sharedAction('notesRecordingUseBtn', 'notes_recording_add_to_note', 'Add to Note', 'submit', { disabled: true }),
            sharedAction('notesRecordingPrimaryBtn', 'notes_recording_start', 'Start Recording', 'submit'),
        ],
    });

    const memoriesImportModal = () => sharedShell({
        id: 'memoriesImportContent',
        overlayClass: 'memories-import-overlay',
        cardClass: 'memories-import-modal shared-modal--wide',
        labelledby: 'memoriesImportModalTitle',
        bodyHtml: `
            <header class="memories-import-modal-header shared-modal-header shared-modal-header--main">
                <div class="memories-import-modal-title-row">
                    <div class="memories-import-modal-icon">${fileIcon()}</div>
                    <div>
                        <h3 class="shared-modal-title" id="memoriesImportModalTitle" data-i18n="workspace_memories_import_modal_title">Import memories</h3>
                        <p class="shared-modal-subtitle" data-i18n="workspace_memories_import_modal_subtitle">Copy the export prompt, run it with another AI, then paste the returned JSON here.</p>
                    </div>
                </div>
                ${closeButton('memoriesImportCloseBtn', 'om-button')}
            </header>
            <div class="memories-import-inline" id="memoriesImportInline">
                <div class="memories-import-modal-body shared-modal-body" id="memoriesImportModalBody">
                    <section class="memories-import-step">
                        <div class="memories-import-step-marker">1</div>
                        <div class="memories-import-step-content">
                            <h4 data-i18n="workspace_memories_import_step_copy_title">Copy this prompt into your other AI provider</h4>
                            <p data-i18n="workspace_memories_import_step_copy_text">It asks for a strict JSON export of every saved memory.</p>
                            <div class="memories-import-prompt-card" id="memoriesImportPromptCard" data-expanded="false">
                                <div class="memories-import-prompt-viewport" id="memoriesImportPromptPreview">
                                    <pre class="memories-import-prompt-text" id="memoriesImportPromptText"></pre>
                                </div>
                                <button type="button" class="memories-import-prompt-toggle" id="memoriesImportPromptToggle" aria-controls="memoriesImportPromptPreview" aria-expanded="false">
                                    <span id="memoriesImportPromptToggleText" data-i18n="workspace_memories_import_prompt_show_more">Show more</span>
                                </button>
                                <button type="button" class="om-button border cancel" id="memoriesImportCopyBtn" data-i18n="workspace_memories_import_prompt_copy">Copy prompt</button>
                            </div>
                        </div>
                    </section>
                    <section class="memories-import-step">
                        <div class="memories-import-step-marker">2</div>
                        <div class="memories-import-step-content">
                            <div class="memories-import-textarea-header">
                                <div>
                                    <h4 data-i18n="workspace_memories_import_step_paste_title">Paste the JSON response</h4>
                                    <p data-i18n="workspace_memories_import_step_paste_text">We validate the format before anything is imported.</p>
                                </div>
                                <button type="button" class="om-button border cancel" id="memoriesImportClearBtn" hidden data-i18n="workspace_memories_import_clear">Clear</button>
                            </div>
                            <label class="sr-only" for="memoriesImportInput" data-i18n="workspace_memories_import_input_label">Memory JSON</label>
                            <textarea id="memoriesImportInput" class="memories-import-textarea" rows="10" spellcheck="false" data-i18n-attr="placeholder:workspace_memories_import_input_placeholder" placeholder='[{"date":"2025-02-01","content":"Always return code in a single file."}]' aria-describedby="memoriesImportError" aria-invalid="false"></textarea>
                            <p class="memories-import-note" data-i18n="workspace_memories_import_input_note">Accepted format: a single JSON array of objects with exactly date and content.</p>
                            <div class="memories-import-error" id="memoriesImportError" hidden>
                                <div class="memories-import-error-icon">${fileIcon()}</div>
                                <p id="memoriesImportErrorMessage"></p>
                            </div>
                            <div class="memories-import-preview" id="memoriesImportPreview" hidden>
                                <div class="memories-import-preview-header">
                                    <div>
                                        <p class="memories-import-preview-summary" id="memoriesImportPreviewSummary"></p>
                                        <p class="memories-import-preview-meta" id="memoriesImportPreviewMeta"></p>
                                    </div>
                                </div>
                                <div class="memories-import-preview-list" id="memoriesImportPreviewList"></div>
                            </div>
                        </div>
                    </section>
                </div>
            </div>
        `,
        actions: [cancel('memoriesImportCancelBtn', 'workspace_memories_import_cancel', 'Cancel'), submit('memoriesImportConfirmBtn', 'workspace_memories_import_confirm', 'Import memories', 'memoriesImportConfirmText', { disabled: true })],
    });

    const meetingModal = ({ id, cardId, titleId, titleI18n, titleText, eyebrowI18n, eyebrowText, subtitleId, subtitleI18n, subtitleText, closeId, bodyHtml, actions, actionsLeadHtml = '' }) => sharedShell({
        id,
        overlayClass: 'chat-meeting-modal-overlay',
        cardId,
        cardClass: 'chat-meeting-modal shared-modal--wide',
        labelledby: titleId,
        describedby: subtitleId,
        bodyHtml: `
            <header class="chat-meeting-modal__header shared-modal-header shared-modal-header--main">
                <div class="chat-meeting-modal__heading">
                    ${eyebrowText ? `<span class="chat-meeting-modal__eyebrow" data-i18n="${eyebrowI18n}">${eyebrowText}</span>` : ''}
                    <h2 class="chat-meeting-modal__title shared-modal-title" id="${titleId}" data-i18n="${titleI18n}">${titleText}</h2>
                    ${subtitleText ? `<p class="chat-meeting-modal__subtitle shared-modal-subtitle"${subtitleId ? ` id="${subtitleId}"` : ''} data-i18n="${subtitleI18n}">${subtitleText}</p>` : ''}
                </div>
                ${closeButton(closeId, 'om-button')}
            </header>
            <div class="chat-meeting-modal__body shared-modal-body">
                ${bodyHtml}
            </div>
        `,
        actions,
        actionsLeadHtml,
    });




    const shareLinkModal = ({ id, titleId, titleI18n, titleText, subtitleId, subtitleI18n, subtitleText, closeId, closeI18n, bodyHtml, actions }) => sharedShell({
        id,
        overlayClass: 'cs-overlay',
        cardClass: 'cs-modal',
        labelledby: titleId,
        bodyHtml: `
            <header class="cs-header shared-modal-header shared-modal-header--main">
                <div class="cs-header-text">
                    <h3 class="cs-title shared-modal-title" id="${titleId}" data-i18n="${titleI18n}">${titleText}</h3>
                    <p class="cs-subtitle shared-modal-subtitle" id="${subtitleId}" data-i18n="${subtitleI18n}">${subtitleText}</p>
                </div>
                ${closeButton(closeId, 'cs-icon-btn', closeI18n, 'Close share dialog')}
            </header>
            <div class="cs-body shared-modal-body">${bodyHtml}</div>
        `,
        actions,
    });

    window.DeleteWarningModal?.mountAll([
        skillAcceptModal(),
        skillImportModal(),
        shareAcceptModal({
            id: 'promptAcceptOverlay',
            titleId: 'promptAcceptTitle',
            ownerId: 'promptAcceptOwner',
            previewId: 'promptAcceptPreview',
            previewContentId: 'promptAcceptPreviewContent',
            confirmId: 'promptAcceptConfirmBtn',
            badgeI18n: 'prompt_share_title',
            badgeText: 'Share Prompt',
            titleI18n: 'workspace_skills_accept_loading',
            titleText: 'Loading...',
            previewI18n: 'prompt_editor_content_label',
            previewText: 'Prompt content',
            confirmI18n: 'workspace_notifications_accept',
            confirmText: 'Accept',
            iconId: 'promptAcceptIcon',
            iconStyle: 'background-color: var(--primary-color);',
            iconSvg: Icons.resolveIcon("memory_management"),
            typeInfoId: 'promptAcceptShareTypeInfo',
            descHtml: '<p class="share-accept-desc prompt-accept-description" id="promptAcceptDescription"></p>',
            describedby: 'promptAcceptDescription',
            overlayAttrs: { 'aria-hidden': 'true' },
        }),
        shareAcceptModal({
            id: 'todoAcceptOverlay',
            titleId: 'todoAcceptTitle',
            ownerId: 'todoAcceptOwner',
            previewId: 'todoAcceptPreview',
            previewContentId: 'todoAcceptPreviewContent',
            confirmId: 'todoAcceptConfirmBtn',
            badgeI18n: 'todos_accept_badge',
            badgeText: 'Shared Todo List',
            titleI18n: 'todos_accept_loading',
            titleText: 'Loading...',
            previewI18n: 'todos_accept_preview_label',
            previewText: 'List preview',
            confirmI18n: 'todos_accept_add_action',
            confirmText: 'Add to My Lists',
            iconId: 'todoAcceptIcon',
            iconStyle: 'background-color: #10b981;',
            iconSvg: Icons.resolveIcon("todo_management"),
            descHtml: '<p class="share-accept-desc" data-i18n="todos_accept_desc">Adding this list will let you view all todos. The owner can make changes that will sync to your workspace.</p>',
        }),
        shareAcceptModal({
            id: 'noteAcceptOverlay',
            titleId: 'noteAcceptTitle',
            ownerId: 'noteAcceptOwner',
            previewId: 'noteAcceptPreview',
            previewContentId: 'noteAcceptPreviewContent',
            confirmId: 'noteAcceptConfirmBtn',
            badgeI18n: 'notes_accept_badge',
            badgeText: 'Shared Note',
            titleI18n: 'notes_accept_loading',
            titleText: 'Loading...',
            previewI18n: 'notes_accept_preview_label',
            previewText: 'Note preview',
            confirmI18n: 'notes_accept_add_action',
            confirmText: 'Add to My Notes',
            iconId: 'noteAcceptIcon',
            iconStyle: 'background-color: #f59e0b;',
            iconSvg: Icons.resolveIcon("notes_management"),
            typeInfoId: 'noteAcceptShareTypeInfo',
        }),
        shareAcceptModal({
            id: 'folderAcceptOverlay',
            titleId: 'folderAcceptTitle',
            ownerId: 'folderAcceptOwner',
            previewId: 'folderAcceptPreview',
            previewContentId: 'folderAcceptPreviewContent',
            confirmId: 'folderAcceptConfirmBtn',
            badgeI18n: 'files_folder_accept_badge',
            badgeText: 'Shared Folder',
            titleI18n: 'files_folder_accept_loading',
            titleText: 'Loading...',
            previewI18n: 'files_folder_accept_preview_label',
            previewText: 'Folder contents',
            confirmI18n: 'files_folder_accept_add',
            confirmText: 'Add to My Files',
            iconId: 'folderAcceptIcon',
            iconStyle: 'background-color: #6366f1;',
            iconSvg: Icons.resolveIcon("folder"),
            typeInfoId: 'folderAcceptShareTypeInfo',
        }),
        notesFilePickerModal(),
        notesRecordingModal(),
        memoriesImportModal(),
        meetingModal({
            id: 'chatBoxMeetingOverlay',
            cardId: 'chatBoxMeetingModal',
            titleId: 'chatBoxMeetingTitle',
            titleI18n: 'chat_meeting_title',
            titleText: 'Add meeting',
            subtitleI18n: 'chat_meeting_subtitle',
            subtitleText: 'Turn an audio or video recording into a transcript saved to this chat.',
            subtitleId: 'chatBoxMeetingSubtitle',
            eyebrowI18n: 'chat_meeting_eyebrow',
            eyebrowText: 'Meeting transcription',
            closeId: 'chatBoxMeetingCloseButton',
            bodyHtml: `
                <div class="chat-meeting-modal__tabs" id="chatBoxMeetingSourceTabs" role="tablist" aria-label="Meeting sources" data-i18n-attr="aria-label:chat_meeting_sources_aria">
                    <span class="chat-meeting-tab-indicator" aria-hidden="true"></span>
                    <button type="button" class="chat-meeting-tab is-active" id="chatBoxMeetingUploadOption" data-meeting-source="upload" role="tab" aria-selected="true" aria-controls="chatBoxMeetingUploadPanel" tabindex="0"><span class="chat-meeting-tab__icon" aria-hidden="true">${uploadIcon()}</span><span class="chat-meeting-tab__label" data-i18n="header_upload">Upload</span></button>
                    <button type="button" class="chat-meeting-tab" id="chatBoxMeetingRecordOption" data-meeting-source="microphone" role="tab" aria-selected="false" aria-controls="chatBoxMeetingCapturePanel" tabindex="-1"><span class="chat-meeting-tab__icon" aria-hidden="true">${microphoneIcon()}</span><span class="chat-meeting-tab__label" data-i18n="notes_recording_source_microphone">Microphone</span></button>
                    <button type="button" class="chat-meeting-tab" id="chatBoxMeetingScreenOption" data-meeting-source="screen" role="tab" aria-selected="false" aria-controls="chatBoxMeetingCapturePanel" tabindex="-1"><span class="chat-meeting-tab__icon" aria-hidden="true">${screenAudioIcon()}</span><span class="chat-meeting-tab__label" data-i18n="chat_meeting_source_screen_audio">Screen audio</span></button>
                </div>
                <div class="chat-meeting-source-panel is-active" id="chatBoxMeetingUploadPanel" data-meeting-panel="upload" role="tabpanel" aria-labelledby="chatBoxMeetingUploadOption">
                    <button type="button" class="chat-meeting-dropzone" id="chatBoxMeetingDropzone" aria-labelledby="chatBoxMeetingDropzoneTitle" aria-describedby="chatBoxMeetingDropzoneCopy chatBoxMeetingDropzoneHint">
                        <span class="chat-meeting-dropzone__icon" aria-hidden="true">${uploadIcon()}</span>
                        <span class="chat-meeting-dropzone__title" id="chatBoxMeetingDropzoneTitle" data-i18n="chat_meeting_choose_recording">Choose a meeting recording</span>
                        <span class="chat-meeting-dropzone__copy" id="chatBoxMeetingDropzoneCopy" data-i18n="chat_meeting_dropzone_copy">Audio and video files supported. Video is converted to audio before transcription.</span>
                        <span class="chat-meeting-dropzone__hint" id="chatBoxMeetingDropzoneHint" data-i18n="chat_meeting_dropzone_hint">Click to browse or drag a file here</span>
                    </button>
                </div>
                <div class="chat-meeting-source-panel" id="chatBoxMeetingCapturePanel" data-meeting-panel="capture" role="tabpanel" aria-labelledby="chatBoxMeetingRecordOption" hidden>
                    <div class="chat-meeting-recorder is-idle">
                        <span class="chat-meeting-recorder__eyebrow" id="chatBoxMeetingCaptureModeLabel">Microphone</span>
                        <strong class="chat-meeting-recorder__timer" id="chatBoxMeetingCaptureTimer" aria-live="off">00:00</strong>
                        <span class="chat-meeting-recorder__status" aria-live="polite"><span class="chat-meeting-recorder__dot" aria-hidden="true"></span><span id="chatBoxMeetingCaptureStatus">Ready to record</span></span>
                        <span class="chat-meeting-recorder__details" id="chatBoxMeetingCaptureDetails">Capture a live meeting, then stop when you are ready to transcribe.</span>
                        <span class="chat-meeting-recorder__equalizer" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></span>
                        <div class="chat-meeting-recorder__actions">
                            <button type="button" class="chat-meeting-record-button" id="chatBoxMeetingCaptureToggleButton" aria-describedby="chatBoxMeetingCaptureHint"><span class="chat-meeting-record-button__icon" aria-hidden="true"></span><span class="sr-only chat-meeting-record-button__label" data-i18n="chat_meeting_start_recording">Start recording</span></button>
                            <button type="button" class="om-button border cancel chat-meeting-recorder__discard" id="chatBoxMeetingCaptureDiscardButton" hidden data-i18n="chat_meeting_discard">Discard</button>
                        </div>
                        <p class="chat-meeting-recorder__hint" id="chatBoxMeetingCaptureHint">When you stop, the recording is attached here and can be transcribed immediately.</p>
                    </div>
                </div>
                <div class="chat-meeting-source-panel chat-meeting-result-panel" id="chatBoxMeetingResultPanel" data-meeting-panel="result" role="tabpanel" aria-labelledby="chatBoxMeetingUploadOption" hidden>
                    <div class="chat-meeting-selection" id="chatBoxMeetingSelection" hidden>
                        <span class="chat-meeting-selection__icon" aria-hidden="true">${fileIcon()}</span>
                        <div class="chat-meeting-selection__meta"><strong class="chat-meeting-selection__name" id="chatBoxMeetingSelectionName">meeting.mp4</strong><span class="chat-meeting-selection__details" id="chatBoxMeetingSelectionDetails">MP4 - 12 MB</span></div>
                        <button type="button" class="chat-meeting-selection__remove" id="chatBoxMeetingClearSelectionButton" aria-label="Remove selected file" data-i18n-attr="aria-label:chat_meeting_remove_selected_file_aria">${closeIcon()}</button>
                    </div>
                    <div class="chat-meeting-progress" id="chatBoxMeetingProgress" role="status" aria-live="polite" hidden>
                        <div class="chat-meeting-progress__meta"><strong id="chatBoxMeetingProgressLabel" data-i18n="chat_meeting_uploading">Uploading meeting...</strong><span id="chatBoxMeetingProgressDetail" data-i18n="chat_meeting_progress_starting">Starting</span></div>
                        <div class="chat-meeting-progress__bar"><span class="chat-meeting-progress__fill" id="chatBoxMeetingProgressFill"></span></div>
                    </div>
                    <section class="chat-meeting-governance" id="chatBoxMeetingGovernance" aria-labelledby="chatBoxMeetingGovernanceTitle" hidden>
                        <div class="chat-meeting-governance__header"><strong class="chat-meeting-governance__title" id="chatBoxMeetingGovernanceTitle" data-i18n="meeting_modal_governance_title">Recording requirements</strong><p class="chat-meeting-governance__copy" data-i18n="meeting_modal_governance_copy">Capture consent or legal basis and retention before transcribing.</p></div>
                        <label class="chat-meeting-governance__checkbox"><input type="checkbox" id="chatBoxMeetingConsentCheckbox" required><span data-i18n="meeting_modal_consent_label">I confirm participants were informed and any required consent was obtained.</span></label>
                        <div class="chat-meeting-governance__grid">
                            <div class="chat-meeting-governance__field">
                                <span class="chat-meeting-governance__label" id="chatBoxMeetingLegalBasisLabel" data-i18n="meeting_modal_legal_basis_label">Legal basis</span>
                                <div class="custom-select chat-meeting-governance__select" id="chatBoxMeetingLegalBasis" data-field="meeting_legal_basis">
                                    <div class="select-trigger" aria-labelledby="chatBoxMeetingLegalBasisLabel" aria-required="true"><span data-i18n="meeting_modal_legal_basis_placeholder">Choose a legal basis</span></div>
                                    <div class="select-options">
                                        <div class="select-option selected" data-value="" data-i18n="meeting_modal_legal_basis_placeholder">Choose a legal basis</div>
                                        <div class="select-option" data-value="consent" data-i18n="meeting_modal_legal_basis_option_consent">Consent</div>
                                        <div class="select-option" data-value="contract" data-i18n="meeting_modal_legal_basis_option_contract">Contract</div>
                                        <div class="select-option" data-value="legitimate_interest" data-i18n="meeting_modal_legal_basis_option_legitimate_interest">Legitimate interest</div>
                                        <div class="select-option" data-value="legal_obligation" data-i18n="meeting_modal_legal_basis_option_legal_obligation">Legal obligation</div>
                                        <div class="select-option" data-value="public_task" data-i18n="meeting_modal_legal_basis_option_public_task">Public task</div>
                                        <div class="select-option" data-value="other" data-i18n="meeting_modal_legal_basis_option_other">Other</div>
                                    </div>
                                </div>
                            </div>
                            <label class="chat-meeting-governance__field" for="chatBoxMeetingRetentionDays"><span class="chat-meeting-governance__label" data-i18n="meeting_modal_retention_days_label">Retention (days)</span><input type="number" min="1" max="3650" step="1" value="30" inputmode="numeric" id="chatBoxMeetingRetentionDays" class="chat-meeting-governance__control" required></label>
                        </div>
                        <label class="chat-meeting-governance__field" for="chatBoxMeetingLegalBasisDetails"><span class="chat-meeting-governance__label" data-i18n="meeting_modal_legal_basis_details_label">Legal-basis details</span><textarea id="chatBoxMeetingLegalBasisDetails" class="chat-meeting-governance__control chat-meeting-governance__textarea" rows="3" maxlength="500" data-i18n-attr="placeholder:meeting_modal_legal_basis_details_placeholder" placeholder="Reference the policy, contract, or consent record used for this meeting." required></textarea></label>
                    </section>
                </div>
            `,
            actions: [cancel('chatBoxMeetingCancelButton'), submit('chatBoxMeetingSubmitButton', 'chat_meeting_transcribe', 'Transcribe meeting', null, { disabled: true })],
            actionsLeadHtml: `<p class="chat-meeting-modal__note">${infoIcon()}<span id="chatBoxMeetingNote" data-i18n="chat_meeting_note">The transcript file will be named from your browser date and added to this conversation as a user file.</span></p>`,
        }),
        shareLinkModal({
            id: 'chatShareOverlay',
            titleId: 'chatShareTitle',
            titleI18n: 'chat_share_modal_title',
            titleText: 'Share chat',
            subtitleId: 'chatShareSubtitle',
            subtitleI18n: 'chat_share_modal_subtitle_default',
            subtitleText: 'Create a link to share this conversation.',
            closeId: 'chatShareCloseBtn',
            closeI18n: 'chat_share_close_aria',
            bodyHtml: `
                <section class="cs-section" id="chatShareLinksSection" hidden><div class="cs-section-head"><span class="cs-section-label" data-i18n="chat_share_active_link_label">Active link</span></div><div class="cs-link-list" id="chatShareLinkList"></div></section>
                <section class="cs-empty" id="chatShareEmptySection" hidden><div class="cs-empty-icon" aria-hidden="true">${linkIcon}</div><p class="cs-empty-title" data-i18n="chat_share_empty_title">No share link yet</p><p class="cs-empty-desc" data-i18n="chat_share_empty_desc">Create a link to share this chat with others.</p></section>
                <section class="cs-form" id="chatShareForm" hidden>
                    <div class="cs-section-head"><span class="cs-section-label" id="chatShareFormTitle" data-i18n="chat_share_create_new_link">Create new link</span></div>
                    <div class="cs-field"><label class="cs-field-label" data-i18n="chat_share_access_label">Who can open this link</label><div class="cs-radio-group" role="radiogroup" aria-label="Shared chat access" data-i18n-attr="aria-label:chat_share_access_aria">
                        <label class="cs-radio"><input type="radio" name="chatShareAccessMode" value="public" id="chatShareAccessPublic" checked><div class="cs-radio-content"><span class="cs-radio-title" data-i18n="chat_share_access_public_title">Anyone with the link</span><span class="cs-radio-desc" data-i18n="chat_share_access_public_desc">No Omlorix account required.</span></div></label>
                        <label class="cs-radio"><input type="radio" name="chatShareAccessMode" value="authenticated" id="chatShareAccessAuthenticated"><div class="cs-radio-content"><span class="cs-radio-title" data-i18n="chat_share_access_authenticated_title">Signed-in users only</span><span class="cs-radio-desc" data-i18n="chat_share_access_authenticated_desc">Requires a valid Omlorix user session.</span></div></label>
                        <label class="cs-radio"><input type="radio" name="chatShareAccessMode" value="invite" id="chatShareAccessInvite"><div class="cs-radio-content"><span class="cs-radio-title" data-i18n="chat_share_access_invite_title">Invite specific users</span><span class="cs-radio-desc" data-i18n="chat_share_access_invite_desc">Send workspace notifications with a signed-in link</span></div></label>
                    </div></div>
                    <div class="cs-field" id="chatSharePasswordField"><div class="cs-toggle-row"><div class="cs-toggle-info"><span class="cs-toggle-label" data-i18n="chat_share_password_toggle_label">Password protection</span><span class="cs-toggle-desc" data-i18n="chat_share_password_toggle_desc">Require a password to open the link</span></div><label class="cs-switch"><input type="checkbox" id="chatSharePasswordToggle"><span class="cs-switch-slider"></span></label></div><div class="cs-toggle-content" id="chatSharePasswordContent" hidden><input type="password" id="chatSharePasswordInput" class="cs-input" placeholder="Enter a password" autocomplete="new-password" data-i18n-attr="placeholder:chat_share_password_placeholder"><p class="cs-helper" id="chatSharePasswordHelper" hidden data-i18n="chat_share_password_keep_help">Leave blank to keep the current password.</p><p class="cs-field-error" id="chatSharePasswordError" hidden data-i18n="chat_share_password_min_error">Password must be at least 8 characters long.</p></div></div>
                    <div class="cs-field" id="chatShareExpiryField"><div class="cs-toggle-row"><div class="cs-toggle-info"><span class="cs-toggle-label" data-i18n="chat_share_expiry_toggle_label">Expiration</span><span class="cs-toggle-desc" data-i18n="chat_share_expiry_toggle_desc">Disable the link automatically at a date and time</span></div><label class="cs-switch"><input type="checkbox" id="chatShareExpiryToggle"><span class="cs-switch-slider"></span></label></div><div class="cs-toggle-content" id="chatShareExpiryContent" hidden><input type="datetime-local" id="chatShareExpiryInput" class="cs-input" aria-describedby="chatShareExpiryError" aria-invalid="false"><p class="cs-field-error" id="chatShareExpiryError" role="alert" hidden></p></div></div>
                    <div class="cs-field cs-publication-field" id="chatSharePublicationField" hidden><p class="cs-field-label" id="chatSharePublicationLabel" data-i18n="chat_share_publication_label">Review published responses</p><p class="cs-helper" id="chatSharePublicationHelp" data-i18n="chat_share_publication_help">Choose the saved answer shown for each regenerated response. Review static tool outputs before publishing them.</p><div class="cs-publication-options" id="chatSharePublicationOptions" role="group" aria-live="polite" aria-labelledby="chatSharePublicationLabel" aria-describedby="chatSharePublicationHelp chatSharePublicationError"></div><p class="cs-field-error" id="chatSharePublicationError" role="alert" hidden></p></div>
                    <div class="cs-field cs-invite-field" id="chatShareInviteField" hidden><label class="cs-field-label" for="chatShareInviteSearch" data-i18n="chat_share_invite_select_label">Select users to invite</label><div class="cs-invite-search">${linkIcon}<input type="text" id="chatShareInviteSearch" class="cs-input cs-invite-search-input" placeholder="Search users..." data-i18n-attr="placeholder:chat_share_invite_search_placeholder" aria-describedby="chatShareInviteError" aria-invalid="false"></div><p class="cs-field-error" id="chatShareInviteError" role="alert" hidden></p><div class="cs-invite-user-list" id="chatShareInviteUserList"><div class="cs-invite-state" data-i18n="chat_share_invite_select_mode_hint">Select "Invite specific users" to load users.</div></div><div class="cs-invite-selected" id="chatShareInviteSelected" hidden><div class="cs-invite-selected-head"><span data-i18n="chat_share_invite_selected_label">Selected</span> (<span id="chatShareInviteSelectedCount">0</span>)</div><div class="cs-invite-selected-list" id="chatShareInviteSelectedList"></div></div></div>
                </section>
                <div class="cs-notice" id="chatShareNotice" hidden></div>
            `,
            actions: [cancel('chatShareSecondaryBtn', 'chat_share_done', 'Done'), submit('chatSharePrimaryBtn', 'chat_share_create_link', 'Create link')],
        }),
        shareLinkModal({
            id: 'promptShareOverlay',
            titleId: 'promptShareTitle',
            titleI18n: 'prompt_share_title',
            titleText: 'Share Prompt',
            subtitleId: 'promptShareSubtitle',
            subtitleI18n: 'prompt_share_subtitle',
            subtitleText: 'Create one or more share links for this prompt.',
            closeId: 'promptShareCloseBtn',
            closeI18n: 'prompt_share_close_aria',
            bodyHtml: `
                <section class="cs-section" id="promptShareLinksSection" hidden><div class="cs-section-head"><span class="cs-section-label" data-i18n="prompt_share_active_links">Active links</span></div><div class="cs-link-list" id="promptShareLinkList"></div></section>
                <section class="cs-empty" id="promptShareEmptySection" hidden><div class="cs-empty-icon" aria-hidden="true">${linkIcon}</div><p class="cs-empty-title" data-i18n="prompt_share_empty_title">No share link yet</p><p class="cs-empty-desc" data-i18n="prompt_share_empty_desc">Create a link to share this prompt with others.</p></section>
                <section class="cs-form" id="promptShareForm" hidden>
                    <div class="cs-section-head"><span class="cs-section-label" id="promptShareFormTitle" data-i18n="prompt_share_create_new_link">Create new link</span></div>
                    <div class="cs-field"><label class="cs-field-label" data-i18n="prompt_share_kind_label">Share kind</label><div class="cs-radio-group" role="radiogroup" aria-label="Prompt share kind" data-i18n-attr="aria-label:prompt_share_kind_aria">
                        <label class="cs-radio"><input type="radio" name="promptShareType" value="live" id="promptShareTypeLive" checked><div class="cs-radio-content"><span class="cs-radio-title" data-i18n="prompt_share_type_live_title">Live</span><span class="cs-radio-desc" data-i18n="prompt_share_type_live_desc">Subscribers receive read-only updates</span></div></label>
                        <label class="cs-radio"><input type="radio" name="promptShareType" value="collaborate" id="promptShareTypeCollaborate"><div class="cs-radio-content"><span class="cs-radio-title" data-i18n="prompt_share_type_collaborate_title">Collaborate</span><span class="cs-radio-desc" data-i18n="prompt_share_type_collaborate_desc">Recipients can work with a synced copy</span></div></label>
                        <label class="cs-radio"><input type="radio" name="promptShareType" value="clone" id="promptShareTypeClone"><div class="cs-radio-content"><span class="cs-radio-title" data-i18n="prompt_share_type_clone_title">Clone</span><span class="cs-radio-desc" data-i18n="prompt_share_type_clone_desc">Creates an independent copy</span></div></label>
                    </div></div>
                    <div class="cs-field"><label class="cs-field-label" data-i18n="prompt_share_delivery_label">Delivery</label><div class="cs-radio-group" role="radiogroup" aria-label="Prompt share delivery" data-i18n-attr="aria-label:prompt_share_delivery_aria"><label class="cs-radio"><input type="radio" name="promptShareAction" value="link" id="promptShareActionLink" checked><div class="cs-radio-content"><span class="cs-radio-title" data-i18n="prompt_share_action_link_title">Create a share link</span><span class="cs-radio-desc" data-i18n="prompt_share_action_link_desc">Generate a reusable link for this share kind</span></div></label><label class="cs-radio"><input type="radio" name="promptShareAction" value="invite" id="promptShareActionInvite"><div class="cs-radio-content"><span class="cs-radio-title" data-i18n="prompt_share_action_invite_title">Invite specific users</span><span class="cs-radio-desc" data-i18n="prompt_share_action_invite_desc">Send a workspace invitation using the selected share kind</span></div></label></div></div>
                    <div class="cs-field cs-invite-field" id="promptShareInviteField" hidden><label class="cs-field-label" for="promptShareInviteSearch" data-i18n="prompt_share_invite_select_label">Select users to invite</label><div class="cs-invite-search">${linkIcon}<input type="text" id="promptShareInviteSearch" class="cs-input cs-invite-search-input" placeholder="Search users..." data-i18n-attr="placeholder:prompt_share_invite_search_placeholder" aria-describedby="promptShareInviteError" aria-invalid="false"></div><p class="cs-field-error" id="promptShareInviteError" role="alert" hidden></p><div class="cs-invite-user-list" id="promptShareInviteUserList"><div class="cs-invite-state" data-i18n="prompt_share_invite_initial_state">Select "Invite specific users" to load users.</div></div><div class="cs-invite-selected" id="promptShareInviteSelected" hidden><div class="cs-invite-selected-head"><span data-i18n="prompt_share_invite_selected_label">Selected</span> (<span id="promptShareInviteSelectedCount">0</span>)</div><div class="cs-invite-selected-list" id="promptShareInviteSelectedList"></div></div></div>
                </section>
                <div class="cs-notice" id="promptShareNotice" aria-hidden="true" aria-live="polite" role="status"></div>
            `,
            actions: [cancel('promptShareSecondaryBtn', 'prompt_share_done', 'Done'), submit('promptSharePrimaryBtn', 'prompt_share_create_link', 'Create link')],
        }),
        shareLinkModal({
            id: 'canvas-artifact-ShareOverlay',
            titleId: 'canvas-artifact-ShareTitle',
            titleI18n: 'canvas_share_title',
            titleText: 'Share Canvas',
            subtitleId: 'canvas-artifact-ShareFileName',
            subtitleI18n: 'canvas_share_selected_file',
            subtitleText: 'Selected file',
            closeId: 'canvas-artifact-ShareCloseBtn',
            closeI18n: 'canvas_share_close_aria',
            bodyHtml: `
                <section class="cs-section" id="canvas-artifact-ShareLinksSection" hidden><div class="cs-section-head"><span class="cs-section-label" data-i18n="canvas_share_active_links">Active links</span></div><div class="cs-link-list" id="canvas-artifact-ShareLinksList"></div></section>
                <section class="cs-empty" id="canvas-artifact-ShareEmptySection" hidden><div class="cs-empty-icon" aria-hidden="true">${linkIcon}</div><p class="cs-empty-title" data-i18n="canvas_share_empty">No share links created yet.</p></section>
                <section class="cs-form" id="canvas-artifact-ShareForm" hidden>
                    <span class="cs-section-label" id="canvas-artifact-ShareFormTitle" data-i18n="chat_share_create_new_link">Create new link</span>
                    <div class="cs-field" id="canvas-artifact-SharePasswordField"><div class="cs-toggle-row"><div class="cs-toggle-info"><span class="cs-toggle-label" data-i18n="chat_share_password_toggle_label">Password protection</span><span class="cs-toggle-desc" data-i18n="chat_share_password_toggle_desc">Require a password to open the link</span></div><label class="cs-switch"><input type="checkbox" id="canvas-artifact-SharePasswordToggle"><span class="cs-switch-slider"></span></label></div><div class="cs-toggle-content" id="canvas-artifact-SharePasswordContent" hidden><input id="canvas-artifact-SharePasswordInput" class="cs-input" type="password" maxlength="200" autocomplete="new-password" placeholder="Enter a password" data-i18n-attr="placeholder:chat_share_password_placeholder" aria-describedby="canvas-artifact-SharePasswordHelper canvas-artifact-SharePasswordError"><p class="cs-helper" id="canvas-artifact-SharePasswordHelper" data-i18n="chat_share_password_keep_help" hidden>Leave blank to keep the current password.</p><p class="cs-field-error" id="canvas-artifact-SharePasswordError" role="alert" hidden></p></div></div>
                    <div class="cs-field" id="canvas-artifact-ShareExpiryField"><div class="cs-toggle-row"><div class="cs-toggle-info"><span class="cs-toggle-label" data-i18n="chat_share_expiry_toggle_label">Expiration</span><span class="cs-toggle-desc" data-i18n="chat_share_expiry_toggle_desc">Disable the link automatically at a date and time</span></div><label class="cs-switch"><input type="checkbox" id="canvas-artifact-ShareExpiryToggle" checked disabled><span class="cs-switch-slider"></span></label></div><div class="cs-toggle-content" id="canvas-artifact-ShareExpiryContent"><input id="canvas-artifact-ShareExpiryInput" class="cs-input" type="datetime-local" aria-label="Expiration date and time" data-i18n-attr="aria-label:chat_share_expiry_toggle_label" aria-describedby="canvas-artifact-ShareExpiryError" aria-invalid="false"><p class="cs-field-error" id="canvas-artifact-ShareExpiryError" role="alert" hidden></p></div></div>
                    <div class="cs-notice" id="canvas-artifact-ShareNotice" hidden></div>
                </section>
            `,
            actions: [cancel('canvas-artifact-ShareSecondaryBtn', 'chat_share_done', 'Done'), submit('canvas-artifact-SharePrimaryBtn', 'chat_share_create_link', 'Create link')],
        }),
        {
            id: 'canvas-html-ExternalResourcesOverlay',
            backdropDismissControlId: 'canvas-html-ExternalResourcesDenyBtn',
            overlayAttrs: { 'aria-hidden': 'true' },
            cardClass: 'canvas-html-external-resource-modal delete-warning-card-wide',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'canvas-html-ExternalResourcesTitle',
            ariaDescribedby: 'canvas-html-ExternalResourcesDesc canvas-html-ExternalResourcesListLabel',
            iconHtml: window.Icons?.globe || window.Icons?.warning || '',
            iconClass: 'delete-warning-card-icon-orange',
            title: title('canvas-html-ExternalResourcesTitle', 'canvas_html_external_prompt_title', 'Allow external connections?'),
            descriptions: [
                desc(
                    'canvas-html-ExternalResourcesDesc',
                    'canvas_html_external_prompt_desc',
                    'This HTML preview wants to load content from outside Omlorix. Allow it only if you trust these connections.',
                ),
            ],
            bodyHtml: `
                <div class="canvas-html-external-resource-review">
                    <p class="canvas-html-external-resource-list-label" id="canvas-html-ExternalResourcesListLabel" data-i18n="canvas_html_external_prompt_list_label">Connections requested</p>
                    <ul class="canvas-html-external-resource-list" id="canvas-html-ExternalResourcesList" aria-labelledby="canvas-html-ExternalResourcesListLabel"></ul>
                </div>
            `,
            actions: [
                cancel('canvas-html-ExternalResourcesDenyBtn', 'canvas_html_external_prompt_deny', 'Keep blocked'),
                submit('canvas-html-ExternalResourcesAllowBtn', 'canvas_html_external_prompt_allow', 'Allow connections'),
            ],
        },
        {
            id: 'modelChangeUnsupportedFilesOverlay',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'modelChangeUnsupportedFilesTitle',
            icon: 'warning',
            title: title('modelChangeUnsupportedFilesTitle', 'model_change_unsupported_title', 'Change model?'),
            descriptions: [desc('modelChangeUnsupportedFilesDesc')],
            actions: [cancel('modelChangeUnsupportedFilesCancel'), submit('modelChangeUnsupportedFilesConfirm', 'common_continue', 'Continue', 'modelChangeUnsupportedFilesConfirmText')],
        },
        {
            id: 'skillsDeleteOverlay',
            icon: 'warning',
            title: title(null, 'workspace_skills_delete_title', 'Delete Skill'),
            descriptions: [htmlDesc('<span data-i18n="workspace_skills_delete_description_prefix">Are you sure you want to delete "</span><span id="skillsDeleteName"></span><span data-i18n="workspace_skills_delete_description_suffix">"? This action cannot be undone.</span>')],
            actions: [cancel('skillsDeleteCancelBtn'), danger('skillsDeleteConfirmBtn', 'workspace_skills_delete_confirm', 'Delete Skill', 'skillsDeleteConfirmText')],
        },
        {
            id: 'changePasswordOverlay',
            backdropDismissControlId: 'changePasswordCancelButton',
            overlayAttrs: { 'aria-hidden': 'true' },
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'changePasswordHeaderTitle',
            iconHtml: window.Icons?.lock || window.Icons?.warning || '',
            title: title('changePasswordHeaderTitle', 'change_password_modal_title', 'Change Password'),
            bodyHtml: `
                <form class="cp-form" id="changePasswordForm">
                    <div class="form-group" id="currentPasswordGroup">
                        <label class="form-label" for="currentPassword" data-i18n="change_password_modal_label_current">Current Password</label>
                        <input id="currentPassword" data-password-role="current" type="password" class="form-input" autocomplete="current-password" placeholder="Current Password" data-i18n-attr="placeholder:change_password_modal_placeholder_current" required aria-invalid="false">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="newPassword" data-i18n="change_password_modal_label_new">New Password</label>
                        <input id="newPassword" data-password-role="new" type="password" class="form-input" autocomplete="new-password" placeholder="New Password" data-i18n-attr="placeholder:change_password_modal_placeholder_new" required aria-invalid="false" aria-describedby="passwordRequirements">
                        <div id="passwordRequirements" class="password-requirements" aria-live="polite"></div>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="confirmPassword" data-i18n="change_password_modal_label_confirm">Confirm New Password</label>
                        <input id="confirmPassword" data-password-role="confirm" type="password" class="form-input" autocomplete="new-password" placeholder="Confirm New Password" data-i18n-attr="placeholder:change_password_modal_placeholder_confirm" required aria-invalid="false" aria-describedby="passwordConfirmError">
                        <div id="passwordConfirmError" class="password-confirm-error" aria-live="polite" hidden></div>
                    </div>
                </form>
            `,
            actions: [
                { id: 'changePasswordCancelButton', role: 'cancel', variant: 'cancel', i18n: 'common_cancel', text: 'Cancel', textId: 'changePasswordCancelBtn' },
                danger('changePasswordBtn', 'change_password_modal_submit', 'Change Password', 'changePasswordBtnText', { type: 'submit', attrs: { form: 'changePasswordForm' } }),
            ],
        },
        {
            id: 'filesDeleteOverlay',
            overlayAttrs: { 'aria-hidden': 'true' },
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'filesDeleteTitle',
            icon: 'warning',
            title: title('filesDeleteTitle', 'files_delete_title', 'Delete file?'),
            descriptions: [
                htmlDesc('<span data-i18n="files_delete_confirm_prefix">Are you sure you want to permanently delete </span><strong id="filesDeleteFileName" data-i18n="files_this_file">this file</strong><span data-i18n="files_delete_confirm_suffix">? This action cannot be undone.</span>'),
                desc(null, 'files_delete_tip', 'Tip: Hold Shift and click Delete to skip this confirmation when cleaning up multiple files.', { class: 'delete-warning-card-desc files-delete-tip' }),
            ],
            actions: [cancel('filesDeleteCancel'), danger('filesDeleteConfirm', 'files_delete_action', 'Delete', 'filesDeleteConfirmText')],
        },
        {
            id: 'filesStorageUsageOverlay',
            overlayAttrs: { 'aria-hidden': 'true' },
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'filesStorageUsageTitle',
            ariaDescribedby: 'filesStorageUsageDesc',
            icon: 'info',
            iconClass: 'files-storage-usage-icon',
            title: title('filesStorageUsageTitle', 'files_storage_usage_title', 'Storage limits'),
            descriptions: [
                desc('filesStorageUsageDesc', 'files_storage_usage_desc', 'Your stored files count toward these workspace limits.'),
            ],
            bodyHtml: `
                <div class="files-storage-usage" id="filesStorageUsageBody">
                    <p class="files-storage-usage-status" id="filesStorageUsageStatus" role="status" aria-live="polite" data-i18n="files_storage_usage_loading">Loading storage usage...</p>
                    <div class="files-storage-usage-meter" id="filesStorageUsageStorageMeter">
                        <div class="files-storage-usage-meter-header">
                            <span class="files-storage-usage-meter-label" data-i18n="files_storage_usage_storage_label">Storage used</span>
                            <span class="files-storage-usage-meter-value" id="filesStorageUsageStorageText"></span>
                        </div>
                        <div class="files-storage-usage-progress" role="progressbar" id="filesStorageUsageStorageProgress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
                            <span class="files-storage-usage-progress-bar" id="filesStorageUsageStorageBar"></span>
                        </div>
                    </div>
                    <div class="files-storage-usage-meter" id="filesStorageUsageCountMeter">
                        <div class="files-storage-usage-meter-header">
                            <span class="files-storage-usage-meter-label" data-i18n="files_storage_usage_files_label">Stored files</span>
                            <span class="files-storage-usage-meter-value" id="filesStorageUsageCountText"></span>
                        </div>
                        <div class="files-storage-usage-progress" role="progressbar" id="filesStorageUsageCountProgress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
                            <span class="files-storage-usage-progress-bar" id="filesStorageUsageCountBar"></span>
                        </div>
                    </div>
                    <p class="files-storage-usage-disabled" id="filesStorageUsageUploadsDisabled" hidden data-i18n="files_storage_usage_uploads_disabled">File uploads are currently disabled for your account.</p>
                </div>
            `,
            actions: [cancel('filesStorageUsageClose', 'files_storage_usage_close', 'Close')],
        },
        {
            id: 'filesFolderDeleteOverlay',
            overlayAttrs: { 'aria-hidden': 'true' },
            icon: 'warning',
            title: title(null, 'files_folder_delete_title', 'Delete folder'),
            descriptions: [htmlDesc('<span data-i18n="files_folder_delete_confirm_prefix">Are you sure you want to delete "</span><span id="filesFolderDeleteName"></span><span data-i18n="files_folder_delete_confirm_middle">"?</span> <span data-i18n="files_folder_delete_confirm_suffix">All files inside will move to</span> <strong data-i18n="files_folder_uncategorized">Uncategorized</strong>.')],
            actions: [cancel('filesFolderDeleteCancel'), danger('filesFolderDeleteConfirm', 'files_folder_delete_action', 'Delete folder')],
        },
        {
            id: 'filesEditModalOverlay',
            cardClass: 'workspace-crud-card files-edit-modal',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'filesEditModalTitle',
            contentHtml: `
                <header class="files-edit-modal-header shared-modal-header shared-modal-header--main">
                    <h3 class="shared-modal-title" id="filesEditModalTitle" data-i18n="files_edit_title">Edit file</h3>
                    <button type="button" class="om-button shared-modal-close" id="filesEditModalClose" aria-label="Close" data-i18n-attr="aria-label:common_close">
                        ${closeIcon()}
                    </button>
                </header>
                <div class="files-edit-modal-body shared-modal-body">
                    <div class="files-edit-modal-description">
                        <p class="files-edit-modal-title" data-i18n="files_edit_description_title">Update your file name</p>
                        <p class="files-edit-modal-text" data-i18n="files_edit_description_text">Choose a name that helps you recognize the file later. You can change it again whenever you need.</p>
                    </div>
                    <div class="files-edit-modal-field">
                        <label for="fileEditNameInput" data-i18n="files_edit_label">File name</label>
                        <input type="text" id="fileEditNameInput" class="files-edit-modal-input" placeholder="Enter new file name" autocomplete="off" data-i18n-attr="placeholder:files_edit_placeholder" aria-describedby="fileEditNameError" aria-invalid="false">
                        <p class="field-validation-error" id="fileEditNameError" data-i18n="files_name_empty_error" aria-hidden="true" hidden>File name cannot be empty</p>
                    </div>
                </div>
            `,
            actions: [cancel('editFileCancelBtn'), submit('saveFileChangesBtn', 'files_edit_save', 'Save changes')],
        },
        {
            id: 'filesFolderModalOverlay',
            cardId: 'filesFolderModal',
            cardClass: 'workspace-crud-card files-folder-modal',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'filesFolderModalTitle',
            contentHtml: `
                <header class="files-folder-modal-header shared-modal-header shared-modal-header--main">
                    <h3 class="shared-modal-title" id="filesFolderModalTitle" data-i18n="files_folder_new">New Folder</h3>
                    <button class="om-button shared-modal-close" id="filesFolderModalClose" type="button" aria-label="Close" data-i18n-attr="aria-label:common_close">
                        ${closeIcon()}
                    </button>
                </header>
                <div class="files-folder-modal-body shared-modal-body">
                    <div class="files-folder-modal-icon-row">
                        <label data-i18n="files_folder_icon_label">Icon & Color</label>
                        <div class="todos-icon-picker" id="filesFolderIconPicker">
                            <button type="button" class="todos-icon-picker-trigger" id="filesFolderIconPickerTrigger">
                                <div class="todos-icon-picker-preview" id="filesFolderIconPickerPreview" style="background-color: #6366f1;"></div>
                                <span class="todos-icon-picker-text" data-i18n="files_folder_icon_choose">Choose icon & color</span>
                                <span class="todos-icon-picker-caret" aria-hidden="true"></span>
                            </button>
                            <div class="todos-icon-picker-dropdown">
                                <div class="todos-icon-picker-section">
                                    <div class="todos-icon-picker-panel active" id="filesFolderSvgPanel" data-panel="svg" role="group" aria-label="Folder icon type" data-i18n-attr="aria-label:files_folder_icon_type_aria">
                                        <div class="todos-icon-grid" id="filesFolderIconGrid"></div>
                                    </div>
                                </div>
                                <div class="todos-icon-picker-section">
                                    <p class="todos-icon-picker-section-title" data-i18n="files_folder_colors">Colors</p>
                                    <div class="todos-color-grid" id="filesFolderColorGrid"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="files-folder-modal-field">
                        <label for="filesFolderNameInput" data-i18n="files_folder_name_label">Folder name</label>
                        <input type="text" id="filesFolderNameInput" class="files-folder-modal-input" placeholder="e.g. Work Documents" maxlength="255" autocomplete="off" data-i18n-attr="placeholder:files_folder_name_placeholder" aria-describedby="filesFolderNameError" aria-invalid="false">
                        <p class="field-validation-error" id="filesFolderNameError" data-i18n="files_folder_name_required" aria-hidden="true" hidden>Folder name is required</p>
                    </div>
                </div>
            `,
            actions: [cancel('filesFolderModalCancel'), submit('filesFolderModalSave', 'files_folder_create', 'Create Folder')],
        },
        {
            id: 'memoriesEditorOverlay',
            cardClass: 'workspace-crud-card memories-editor-modal',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'memoriesFormTitle',
            overlayAttrs: { 'aria-hidden': 'true' },
            contentHtml: `
                <header class="memories-editor-modal-header shared-modal-header shared-modal-header--main">
                    <div>
                        <h3 class="shared-modal-title" id="memoriesFormTitle" data-i18n="workspace_memories_form_create_title">Create memory</h3>
                        <p class="shared-modal-subtitle" id="memoriesFormSubtitle" data-i18n="workspace_memories_form_create_subtitle">Save a concise fact the assistant should remember later.</p>
                    </div>
                    <button type="button" class="om-button shared-modal-close" id="memoriesEditorCloseBtn" aria-label="Close" data-i18n-attr="aria-label:common_close">
                        ${closeIcon()}
                    </button>
                </header>
                <div class="memories-editor-modal-body shared-modal-body">
                    <div class="delete-warning-card-form memories-editor-form">
                        <div class="form-group">
                            <label class="form-label" for="memoriesContentInput" data-i18n="workspace_memories_content_label">Memory</label>
                            <textarea id="memoriesContentInput" class="form-input memories-editor-textarea" rows="5" maxlength="500" placeholder="Prefers terse answers and Python examples." data-i18n-attr="placeholder:workspace_memories_content_placeholder" aria-describedby="memoriesContentError" aria-invalid="false"></textarea>
                            <p class="field-validation-error" id="memoriesContentError" data-i18n="workspace_memories_error_content_required" aria-hidden="true" hidden>Memory content is required</p>
                        </div>
                        <p class="memories-form-meta" id="memoriesMetaText" data-i18n="workspace_memories_form_meta">Only store durable facts or preferences that will matter again later.</p>
                    </div>
                </div>
            `,
            actions: [
                submit('memoriesSaveBtn', 'workspace_memories_save', 'Save Memory'),
            ],
        },
        {
            id: 'notesDeleteOverlay',
            overlayAttrs: { 'aria-hidden': 'true' },
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'notesDeleteTitle',
            icon: 'warning',
            title: title('notesDeleteTitle', 'notes_delete_title', 'Delete Note'),
            descriptions: [desc(null, 'notes_delete_desc', 'This note and its version history will be permanently deleted. This action cannot be undone.')],
            actions: [cancel('notesDeleteCancelBtn'), danger('notesDeleteConfirmBtn', 'common_delete', 'Delete')],
        },
        {
            id: 'promptDeleteOverlay',
            overlayAttrs: { 'aria-hidden': 'true' },
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'promptDeleteTitle',
            icon: 'warning',
            title: title('promptDeleteTitle', 'prompt_library_delete_title', 'Delete Prompt'),
            descriptions: [desc('promptDeleteDescription', 'prompt_library_delete_desc', 'Are you sure you want to delete "{title}"? This action cannot be undone.')],
            actions: [cancel('promptDeleteCancelBtn'), danger('promptDeleteConfirmBtn', 'prompt_library_delete_confirm', 'Delete Prompt', 'promptDeleteConfirmText')],
        },
        {
            id: 'presetDeleteOverlay',
            icon: 'warning',
            title: title(null, 'preset_delete_title', 'Delete Preset'),
            descriptions: [desc('presetDeleteMessage', 'model_settings_delete_preset_confirm', 'Are you sure you want to delete this preset?')],
            actions: [cancel('presetDeleteCancelBtn'), danger('presetDeleteConfirmBtn', 'common_delete', 'Delete')],
        },
        {
            id: 'deleteAccountOverlay',
            backdropDismissControlId: 'deleteAccountCancelButton',
            overlayAttrs: { 'aria-hidden': 'true' },
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'deleteAccountHeaderTitle',
            ariaDescribedby: 'deleteAccountDescription deleteAccountPolicyText deleteAccountPurgeText',
            icon: 'warning',
            title: title('deleteAccountHeaderTitle', 'delete_account_title', 'Delete Account'),
            descriptions: [
                desc('deleteAccountDescription', 'delete_account_confirm', 'Are you sure you want to delete your account?'),
                desc('deleteAccountPolicyText'),
                desc('deleteAccountPurgeText'),
            ],
            actions: [cancel('deleteAccountCancelButton'), danger('deleteAccountPrimaryButton', 'delete_account_confirm_button', 'Delete Account', 'deleteAccountPrimaryText')],
        },
        {
            id: 'logoutAllDevicesOverlay',
            icon: 'warning',
            title: title('logoutAllDevicesHeaderTitle', 'logout_all_devices_title', 'Logout All Devices'),
            descriptions: [desc(null, 'logout_all_devices_confirm', 'Are you sure you want to logout all devices?')],
            actions: [cancel('logoutAllDevicesCancelButton'), danger('logoutAllDevicesPrimaryButton', 'logout_all_devices_confirm_button', 'Logout All Devices', 'logoutAllDevicesPrimaryText')],
        },
        {
            id: 'deletePasskeyOverlay',
            icon: 'warning',
            title: title(null, 'passkey_delete_title', 'Remove Passkey'),
            descriptions: [desc('deletePasskeyDescription')],
            actions: [cancel('deletePasskeyCancelButton'), danger('deletePasskeyConfirmButton', 'passkey_delete_confirm', 'Remove Passkey')],
        },
        {
            id: 'deleteAllChatsOverlay',
            icon: 'warning',
            title: title('deleteAllChatsHeaderTitle', 'delete_all_chats_title', 'Delete All Chats'),
            descriptions: [desc('deleteAllChatsDescription', 'delete_all_chats_confirm', 'Are you sure you want to delete all chats?')],
            actions: [cancel('deleteAllChatsCancelButton'), danger('deleteAllChatsPrimaryButton', 'delete_all_chats_confirm_button', 'Delete All Chats', 'deleteAllChatsPrimaryText')],
        },
        {
            id: 'deleteAllFilesOverlay',
            icon: 'warning',
            title: title('deleteAllFilesHeaderTitle', 'delete_all_files_title', 'Delete Files'),
            descriptions: [desc(null, 'delete_all_files_confirm', 'Are you sure you want to delete the selected files?')],
            bodyHtml: `
                <div class="delete-warning-card-form">
                    <div class="form-group">
                        <label class="form-label" for="deleteAllFilesScopeSelect" data-i18n="delete_all_files_scope_label">Choose which files to delete</label>
                        <div class="custom-select us" id="deleteAllFilesScopeSelect">
                            <div class="select-trigger"><span data-i18n="delete_all_files_option_all">All files</span></div>
                            <div class="select-options">
                                <div class="select-option selected" data-value="all" data-i18n="delete_all_files_option_all">All files</div>
                                <div class="select-option" data-value="websearch" data-i18n="delete_all_files_option_websearch">Web search files only</div>
                            </div>
                        </div>
                        <input type="hidden" id="deleteAllFilesScopeValue" value="all">
                    </div>
                    <div class="form-group" id="deleteAllFilesTimeGroup">
                        <label class="form-label" for="deleteAllFilesTimeSelect" data-i18n="delete_all_files_filter_label">Delete files that are</label>
                        <div class="custom-select us" id="deleteAllFilesTimeSelect">
                            <div class="select-trigger"><span data-i18n="delete_all_files_option_all">All files</span></div>
                            <div class="select-options">
                                <div class="select-option selected" data-value="all" data-i18n="delete_all_files_option_all">All files</div>
                                <div class="select-option" data-value="older_than_1_day" data-i18n="delete_all_files_option_day">Older than 1 day</div>
                                <div class="select-option" data-value="older_than_1_week" data-i18n="delete_all_files_option_week">Older than 1 week</div>
                                <div class="select-option" data-value="older_than_1_month" data-i18n="delete_all_files_option_month">Older than 1 month</div>
                                <div class="select-option" data-value="older_than_1_year" data-i18n="delete_all_files_option_year">Older than 1 year</div>
                            </div>
                        </div>
                        <input type="hidden" id="deleteAllFilesTimeValue" value="all">
                    </div>
                </div>
            `,
            actions: [cancel('deleteAllFilesCancelButton'), danger('deleteAllFilesPrimaryButton', 'delete_all_files_confirm_button', 'Delete Files', 'deleteAllFilesPrimaryText')],
        },
        {
            id: 'filesDeleteWebsearchOverlay',
            icon: 'warning',
            title: title('filesDeleteWebsearchTitle', 'files_delete_websearch_title', 'Delete all web search files?'),
            descriptions: [desc('filesDeleteWebsearchMessage', 'files_delete_websearch_message', 'This will permanently remove every file uploaded from web search results. This action cannot be undone.')],
            actions: [cancel('filesDeleteWebsearchCancel'), danger('filesDeleteWebsearchConfirm', 'files_delete_websearch_confirm', 'Delete')],
        },
        {
            id: 'deleteChatOverlay',
            icon: 'warning',
            title: title('deleteChatTitle', 'chat_delete_title', 'Delete Chat'),
            descriptions: [desc(null, 'chat_delete_warning_text1', 'Deleting a chat removes all messages and history associated with it.')],
            actions: [cancel('deleteChatCancelBtn'), danger('confirmDeleteChatBtn', 'chat_delete_confirm', 'Delete chat', 'deleteChatPrimaryText')],
        },
        {
            id: 'deleteMessageOverlay',
            icon: 'warning',
            title: title('deleteMessageTitle', 'delete_message_title', 'Delete Message'),
            descriptions: [desc('deleteMessageDescription', 'delete_message_description', 'Deleting a user message will also remove every message below it in this chat. This action cannot be undone.')],
            actions: [cancel('deleteMessageCancelBtn'), danger('confirmDeleteMessageBtn', 'delete_message_and_below', 'Delete message and below', 'deleteMessagePrimaryText')],
        },
        {
            id: 'editChatOverlay',
            icon: 'file',
            title: title('editChatModalTitle', 'chat_edit_title', 'Edit Chat'),
            bodyHtml: `
                <div class="edit-chat-form">
                    <div class="edit-chat-input-group">
                        <label for="editChatNameInput" data-i18n="chat_edit_name_label">Chat name</label>
                        <div class="edit-chat-input-row">
                            <input type="text" id="editChatNameInput" placeholder="Enter new chat name" class="edit-chat-input" data-i18n-attr="placeholder:chat_edit_name_placeholder">
                        </div>
                    </div>
                </div>
            `,
            actions: [cancel('editChatCancelBtn'), submit('confirmEditChatBtn', 'chat_edit_save', 'Save changes', 'editChatPrimaryText')],
        },
        {
            id: 'deleteProjectOverlay',
            icon: 'warning',
            title: title('deleteProjectTitle', 'projects_delete_title', 'Delete project'),
            descriptions: [
                desc(null, 'projects_delete_warning_text1', 'Deleting a project removes all attachments and contextual information associated with it. Chats themselves remain.'),
                desc(null, 'projects_delete_warning_text2', 'Please confirm the project you want to remove.'),
            ],
            actions: [cancel('deleteProjectCancelBtn'), danger('confirmDeleteProjectBtn', 'projects_delete_confirm', 'Delete project', 'deleteProjectPrimaryText')],
        },
        {
            id: 'deleteAutomationOverlay',
            icon: 'warning',
            title: title('deleteAutomationTitle', 'automations_delete_title', 'Delete automation'),
            descriptions: [desc(null, 'automations_delete_warning_text', 'Deleting an automation will stop all future scheduled executions. Past chat history from this automation will remain.')],
            actions: [cancel('deleteAutomationCancelBtn'), danger('confirmDeleteAutomationBtn', 'automations_delete_confirm', 'Delete automation', 'deleteAutomationPrimaryText')],
        },
        {
            id: 'notesRestoreOverlay',
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: 'notesRestoreTitle',
            icon: 'file',
            title: title('notesRestoreTitle', 'notes_restore_title', 'Restore this version?'),
            descriptions: [desc('notesRestoreDescription', null, '')],
            bodyHtml: `
                <div class="notes-restore-meta" aria-live="polite">
                    <div class="notes-restore-meta-item"><span class="notes-restore-meta-label" data-i18n="notes_restore_edited_by">Edited by</span><span class="notes-restore-meta-value" id="notesRestoreAuthor"></span></div>
                    <div class="notes-restore-meta-item"><span class="notes-restore-meta-label" data-i18n="notes_restore_edited_on">Edited on</span><span class="notes-restore-meta-value" id="notesRestoreTimestamp"></span></div>
                    <div class="notes-restore-meta-item"><span class="notes-restore-meta-label" data-i18n="notes_restore_summary">Summary</span><span class="notes-restore-meta-value" id="notesRestoreSummary"></span></div>
                </div>
            `,
            actions: [
                cancel('notesRestoreCancelBtn'),
                {
                    id: 'notesRestoreConfirmBtn',
                    variant: 'submit',
                    i18n: 'notes_restore_confirm',
                    text: 'Restore',
                },
            ],
        },
    ]);
})();
