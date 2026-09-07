/**
 * Dynamic create/edit page definitions for Workspace features.
 *
 * Skills and Prompt Library use the same page, field, description, icon, and
 * action primitives as Projects and Automations. Feature-specific content is
 * kept in small helpers so complex fields remain readable without duplicating
 * the surrounding form structure.
 */
(function mountWorkspaceFeatureForms(global) {
    'use strict';

    const renderer = global.CreateEditFormRenderer;
    if (!renderer) {
        throw new Error('CreateEditFormRenderer must load before workspaceCreateEditForms.js');
    }

    /** Render a translated element through the shared renderer. */
    function translated(tag, key, fallback, options = {}) {
        return renderer.renderTranslatedElement({ tag, key, fallback, ...options });
    }

    /** Render the compact icon/name row used by both Skill modes. */
    function renderSkillNameField(mode) {
        const isEdit = mode === 'edit';
        const prefix = isEdit ? 'skillEdit' : 'skill';
        const inputId = isEdit ? 'skillEditTitleInput' : 'skillNameInput';
        const errorId = isEdit ? 'skillEditTitleError' : 'skillNameError';
        const picker = renderer.renderIconPicker({
            idPrefix: prefix,
            pickerId: `${prefix}IconPicker`,
            svgPanelId: `${prefix}IconSvgPanel`,
            previewHtml: '',
            triggerTranslationKey: 'common_icon_choose',
            triggerFallback: 'Choose icon',
            typeTranslationKey: 'skills_icon_type_aria',
            typeFallback: 'Skill icon type',
        });
        const input = renderer.renderControl({
            id: inputId,
            placeholder: isEdit ? 'Enter skill title' : 'my-coding-style',
            placeholderKey: isEdit ? 'skills_edit_title_placeholder' : 'skills_create_name_placeholder',
            attributes: isEdit ? {} : { pattern: '^[a-z0-9]+(?:-[a-z0-9]+)*$' },
        });
        const hint = isEdit ? '' : renderer.renderFieldMessage({
            key: 'skills_create_name_hint',
            fallback: 'Use lowercase letters, numbers, and hyphens only (e.g., my-skill-name)',
        }, 'skills-input-hint');
        const error = renderer.renderFieldMessage({
            id: errorId,
            key: isEdit ? 'skills_edit_title_error' : 'skills_create_name_error',
            fallback: isEdit
                ? 'Please enter a skill title'
                : 'Name must use lowercase letters, numbers, and hyphens only (e.g., my-skill-name)',
        }, 'skills-input-error');

        return renderer.renderField({
            label: {
                key: isEdit ? 'skills_edit_title_label' : 'skills_create_name_label',
                fallback: isEdit ? 'Skill title' : 'Skill name',
                attributes: { for: inputId },
            },
            contentHtml: `
                <div class="projects-name-and-icon-row">
                    ${picker}
                    <div class="projects-name-input-field">${input}${hint}${error}</div>
                </div>`,
        });
    }

    /** Render one standard Skill text or textarea field. */
    function renderSkillControlField({
        id,
        labelKey,
        labelFallback,
        placeholderKey,
        placeholderFallback,
        tag = 'input',
        rows,
        attributes = {},
        error,
        hints = [],
    }) {
        const helperMarkup = hints.map((hint) => renderer.renderFieldMessage(hint, 'skills-input-hint')).join('');
        return renderer.renderControlField({
            label: { key: labelKey, fallback: labelFallback },
            control: {
                tag,
                id,
                placeholder: placeholderFallback,
                placeholderKey,
                attributes: { ...(rows ? { rows } : {}), ...attributes },
            },
            afterControlHtml: helperMarkup,
            error,
        });
    }

    /** Render one editable Skill resource category. */
    function renderSkillFileSection({ kind, titleKey, titleFallback, descriptionKey, descriptionFallback, uploadKey, uploadFallback, iconHtml }) {
        const prefix = `skillEdit${kind}`;
        return `
            <div class="skill-files-section">
                <div class="skill-files-section-header">
                    <h4 class="skill-files-section-title">
                        ${iconHtml}
                        ${translated('span', titleKey, titleFallback)}
                    </h4>
                    ${translated('p', descriptionKey, descriptionFallback, { className: 'skill-files-section-desc' })}
                </div>
                <div class="skill-files-list" id="${prefix}List"></div>
                <div class="skill-files-upload">
                    <input type="file" id="${prefix}Input" multiple hidden>
                    <button type="button" class="om-button border ghost" id="${prefix}Btn" data-i18n="${uploadKey}">
                        ${renderer.renderIcon('upload', { 'aria-hidden': 'true' })}
                        ${uploadFallback}
                    </button>
                </div>
            </div>`;
    }

    /** Build the Create Skill body. */
    function renderCreateSkillBody() {
        return [
            renderer.renderDescription({
                className: 'projects-create-description',
                titleClass: 'projects-create-description-title',
                title: { key: 'skills_create_howto_title', fallback: 'How skills work' },
                textClass: 'projects-create-description-text',
                paragraphs: [
                    {
                        key: 'skills_create_howto_text1',
                        fallback: 'Skills are custom instructions that help the AI understand your preferences, expertise, or specific requirements. They are automatically applied to enhance responses.',
                    },
                    {
                        key: 'skills_create_howto_text2',
                        fallback: 'Give your skill a unique name (lowercase letters, numbers, and hyphens only) and describe what the AI should know or do.',
                    },
                ],
            }),
            renderSkillNameField('create'),
            renderSkillControlField({
                id: 'skillDescriptionInput',
                labelKey: 'skills_create_description_label',
                labelFallback: 'Short description',
                placeholderKey: 'skills_create_description_placeholder',
                placeholderFallback: 'Helps format code in my preferred style',
                error: {
                    id: 'skillDescriptionError',
                    key: 'skills_create_description_error',
                    fallback: 'Please enter a short description',
                },
            }),
            renderSkillControlField({
                id: 'skillContentInput',
                labelKey: 'skills_create_content_label',
                labelFallback: 'Skill instructions',
                placeholderKey: 'skills_create_content_placeholder',
                placeholderFallback: 'Describe what this skill should do...',
                tag: 'textarea',
                rows: 6,
                error: {
                    id: 'skillContentError',
                    key: 'skills_create_content_error',
                    fallback: 'Please enter skill instructions',
                },
            }),
            renderSkillControlField({
                id: 'skillCompatibilityInput',
                labelKey: 'skills_create_compatibility_label',
                labelFallback: 'Compatibility (optional)',
                placeholderKey: 'skills_create_compatibility_placeholder',
                placeholderFallback: 'e.g., gpt-4, claude-3',
            }),
            renderSkillControlField({
                id: 'skillLicenseInput',
                labelKey: 'skills_create_license_label',
                labelFallback: 'License (optional)',
                placeholderKey: 'skills_create_license_placeholder',
                placeholderFallback: 'e.g., MIT, Apache-2.0',
            }),
            renderSkillControlField({
                id: 'skillMetadataInput',
                labelKey: 'skills_create_metadata_label',
                labelFallback: 'Metadata (optional JSON)',
                placeholderKey: 'skills_create_metadata_placeholder',
                placeholderFallback: '{"key": "value"}',
                tag: 'textarea',
                rows: 3,
                attributes: {
                    'aria-describedby': 'skillMetadataHint skillMetadataFilesHint skillMetadataError',
                    'aria-invalid': 'false',
                },
                hints: [
                    { id: 'skillMetadataHint', key: 'skills_create_metadata_hint', fallback: 'Enter valid JSON format' },
                    { id: 'skillMetadataFilesHint', key: 'skills_create_files_hint', fallback: 'After creating the skill you can upload scripts, references, and assets from the Edit view.' },
                ],
                error: {
                    id: 'skillMetadataError',
                    key: 'workspace_skills_validation_metadata_invalid',
                    fallback: 'Invalid JSON in metadata field',
                    attributes: { hidden: true, 'aria-hidden': 'true' },
                },
            }),
        ].join('');
    }

    /** Build the Edit Skill body, including its feature-specific resource lists. */
    function renderEditSkillBody() {
        const fileSections = [
            {
                kind: 'Scripts',
                titleKey: 'workspace_skills_files_scripts_title',
                titleFallback: 'Scripts',
                descriptionKey: 'workspace_skills_files_scripts_desc',
                descriptionFallback: 'Executable scripts and automation files',
                uploadKey: 'workspace_skills_files_scripts_upload',
                uploadFallback: 'Upload Scripts',
                iconHtml: renderer.renderIcon('tool', { 'aria-hidden': 'true' }),
            },
            {
                kind: 'References',
                titleKey: 'workspace_skills_files_references_title',
                titleFallback: 'References',
                descriptionKey: 'workspace_skills_files_references_desc',
                descriptionFallback: 'Documentation, templates, and reference materials',
                uploadKey: 'workspace_skills_files_references_upload',
                uploadFallback: 'Upload References',
                iconHtml: renderer.renderIcon('notes_management', { 'aria-hidden': 'true' }),
            },
            {
                kind: 'Assets',
                titleKey: 'workspace_skills_files_assets_title',
                titleFallback: 'Assets',
                descriptionKey: 'workspace_skills_files_assets_desc',
                descriptionFallback: 'Images, data files, and other resources',
                uploadKey: 'workspace_skills_files_assets_upload',
                uploadFallback: 'Upload Assets',
                iconHtml: renderer.renderIcon('image_gen', { 'aria-hidden': 'true' }),
            },
        ].map(renderSkillFileSection).join('');
        return [
            renderSkillNameField('edit'),
            renderSkillControlField({
                id: 'skillEditContentInput',
                labelKey: 'skills_edit_content_label',
                labelFallback: 'Skill instructions',
                placeholderKey: 'skills_edit_content_placeholder',
                placeholderFallback: 'Describe what this skill should do...',
                tag: 'textarea',
                rows: 6,
                error: {
                    id: 'skillEditContentError',
                    key: 'skills_edit_content_error',
                    fallback: 'Please enter skill instructions',
                },
            }),
            renderSkillControlField({
                id: 'skillEditCompatibilityInput',
                labelKey: 'skills_edit_compatibility_label',
                labelFallback: 'Compatibility (optional)',
                placeholderKey: 'skills_edit_compatibility_placeholder',
                placeholderFallback: 'e.g., gpt-4, claude-3',
            }),
            renderSkillControlField({
                id: 'skillEditLicenseInput',
                labelKey: 'skills_edit_license_label',
                labelFallback: 'License (optional)',
                placeholderKey: 'skills_edit_license_placeholder',
                placeholderFallback: 'e.g., MIT, Apache-2.0',
            }),
            renderSkillControlField({
                id: 'skillEditMetadataInput',
                labelKey: 'skills_edit_metadata_label',
                labelFallback: 'Metadata (optional JSON)',
                placeholderKey: 'skills_edit_metadata_placeholder',
                placeholderFallback: '{"key": "value"}',
                tag: 'textarea',
                rows: 3,
                attributes: {
                    'aria-describedby': 'skillEditMetadataHint skillEditMetadataError',
                    'aria-invalid': 'false',
                },
                hints: [{ id: 'skillEditMetadataHint', key: 'skills_edit_metadata_hint', fallback: 'Enter valid JSON format' }],
                error: {
                    id: 'skillEditMetadataError',
                    key: 'workspace_skills_validation_metadata_invalid',
                    fallback: 'Invalid JSON in metadata field',
                    attributes: { hidden: true, 'aria-hidden': 'true' },
                },
            }),
            fileSections,
        ].join('');
    }

    /** Build the Prompt Library create/edit editor body. */
    function renderPromptEditorBody() {
        return [
            renderer.renderDescription({
                className: 'projects-create-description',
                titleClass: 'projects-create-description-title',
                title: { key: 'prompt_editor_howto_title', fallback: 'How prompts work' },
                textClass: 'projects-create-description-text',
                paragraphs: [{
                    key: 'prompt_editor_howto_text',
                    fallback: 'Prompts are reusable templates you can attach from chat with @. Use them for repeated instructions, workflows, or writing patterns.',
                }],
            }),
            renderer.renderControlField({
                label: { key: 'prompt_editor_title_label', fallback: 'Title' },
                control: {
                    id: 'promptEditorTitleInput',
                    placeholder: 'Prompt title',
                    placeholderKey: 'prompt_editor_title_placeholder',
                    attributes: {
                        maxlength: 140,
                        required: true,
                        'aria-describedby': 'promptEditorTitleError',
                        'aria-invalid': 'false',
                    },
                },
                error: {
                    id: 'promptEditorTitleError',
                    key: 'prompt_editor_title_required',
                    fallback: 'Please enter a prompt title',
                    attributes: { 'aria-hidden': 'true' },
                },
            }),
            renderer.renderControlField({
                label: { key: 'prompt_editor_description_label', fallback: 'Description' },
                control: {
                    id: 'promptEditorDescriptionInput',
                    placeholder: 'Short summary',
                    placeholderKey: 'prompt_editor_description_placeholder',
                    attributes: { maxlength: 500 },
                },
            }),
            renderer.renderControlField({
                label: { key: 'prompt_editor_content_label', fallback: 'Prompt content' },
                control: {
                    tag: 'textarea',
                    id: 'promptEditorContentInput',
                    placeholder: 'Write the reusable prompt content...',
                    placeholderKey: 'prompt_editor_content_placeholder',
                    attributes: { rows: 10 },
                },
            }),
            `<p class="prompt-editor-revision-meta" id="promptEditorRevisionMeta" role="status" aria-live="polite"></p>`,
            `<section class="prompt-editor-conflict" id="promptEditorConflict" role="alert" aria-labelledby="promptEditorConflictTitle" hidden>
                <div class="prompt-editor-conflict-header">
                    <div>
                        ${translated('h3', 'prompt_conflict_title', 'This prompt changed while you were editing', { id: 'promptEditorConflictTitle' })}
                        ${translated('p', 'prompt_conflict_description', 'Compare the saved version with your draft, then choose how to continue.')}
                    </div>
                </div>
                <div class="prompt-editor-conflict-grid">
                    <div>
                        ${translated('h4', 'prompt_conflict_your_draft', 'Your draft')}
                        <pre id="promptConflictLocalContent"></pre>
                    </div>
                    <div>
                        ${translated('h4', 'prompt_conflict_saved_version', 'Latest saved version')}
                        <p class="prompt-editor-conflict-author" id="promptConflictRemoteMeta"></p>
                        <pre id="promptConflictRemoteContent"></pre>
                    </div>
                </div>
                <div class="prompt-editor-conflict-actions">
                    <button type="button" class="om-button border" id="promptConflictCopyBtn" data-i18n="prompt_conflict_copy_draft">Copy my draft</button>
                    <button type="button" class="om-button border" id="promptConflictReloadBtn" data-i18n="prompt_conflict_reload_latest">Use latest saved version</button>
                    <button type="button" class="om-button border submit" id="promptConflictKeepBtn" data-i18n="prompt_conflict_keep_draft">Save my draft as the next version</button>
                </div>
            </section>`,
        ].join('');
    }

    /** Render a two-column remote-connection field group. */
    function renderRemoteGrid(fields) {
        return `<div class="remote-connection-form-grid">${fields.join('')}</div>`;
    }

    /** Render an IconPicker mount with translated guidance. */
    function renderRemoteIconField({ pickerId, helpKey, helpFallback }) {
        return renderer.renderField({
            label: { key: 'workspace_remote_icon_label', fallback: 'Icon' },
            contentHtml: `
                ${translated('p', helpKey, helpFallback, { className: 'remote-connection-field-help' })}
                <div class="remote-connection-icon-picker" id="${pickerId}"></div>`,
        });
    }

    /** Render the Enabled setting shared by SSH and ACP editors. */
    function renderRemoteEnabledToggle({ id, helpKey, helpFallback }) {
        return renderer.renderToggleCard({
            className: 'memories-card remote-connection-toggle-card',
            id,
            label: { key: 'workspace_connections_enabled', fallback: 'Enabled' },
            description: { key: helpKey, fallback: helpFallback },
            inputAttributes: { checked: true },
        });
    }

    /** Render an inline destructive confirmation without using browser dialogs. */
    function renderRemoteDeleteConfirmation({ kind, titleKey, titleFallback, textKey, textFallback }) {
        return `
            <div class="remote-connection-inline-confirmation" id="${kind}DeleteConfirmation" hidden>
                <div>
                    ${translated('strong', titleKey, titleFallback)}
                    ${translated('p', textKey, textFallback)}
                </div>
                <div class="remote-connection-inline-actions">
                    ${translated('button', 'common_cancel', 'Cancel', {
                        className: 'om-button border',
                        id: `cancelDelete${kind[0].toUpperCase()}${kind.slice(1)}Btn`,
                        attributes: { type: 'button' },
                    })}
                    ${translated('button', 'workspace_connections_remove', 'Remove', {
                        className: 'om-button border danger',
                        id: `confirmDelete${kind[0].toUpperCase()}${kind.slice(1)}Btn`,
                        attributes: { type: 'button' },
                    })}
                </div>
            </div>`;
    }

    /** Build the SSH connection editor body. */
    function renderSshEditorBody() {
        const hostKeyField = renderer.renderControlField({
            label: { key: 'workspace_ssh_host_key', fallback: 'Pinned host key' },
            control: {
                tag: 'textarea',
                id: 'sshHostKey',
                className: 'projects-create-textarea connections-secret-textarea',
                placeholder: 'ssh-ed25519 AAAA...',
                placeholderKey: 'workspace_ssh_host_key_placeholder',
                attributes: { required: true, rows: 3 },
            },
            afterControlHtml: `
                <div class="remote-connection-inline-actions">
                    ${translated('button', 'workspace_ssh_discover_key', 'Discover host keys', {
                        className: 'om-button border',
                        id: 'discoverSshKeyBtn',
                        attributes: { type: 'button' },
                    })}
                    ${translated('span', 'workspace_ssh_verify_key_help', 'Compare the fingerprint with the device before saving.', { className: 'remote-connection-field-help' })}
                </div>
                <div id="sshDiscoveredKeys" class="connections-tool-list" aria-live="polite"></div>`,
        });
        const privateKeyField = renderer.renderControlField({
            label: { key: 'workspace_ssh_private_key', fallback: 'Private key' },
            control: {
                tag: 'textarea',
                id: 'sshPrivateKey',
                className: 'projects-create-textarea connections-secret-textarea',
                attributes: {
                    rows: 7,
                    autocomplete: 'off',
                    spellcheck: 'false',
                    'aria-describedby': 'sshPrivateKeyHelp sshPrivateKeyStatus',
                },
            },
            afterControlHtml: `
                <div class="remote-private-key-actions">
                    ${translated('button', 'workspace_ssh_choose_key_file', 'Choose key file', {
                        className: 'om-button border',
                        id: 'chooseSshPrivateKeyBtn',
                        attributes: { type: 'button' },
                    })}
                    <input id="sshPrivateKeyFile" type="file" hidden tabindex="-1">
                    <span class="remote-private-key-status" id="sshPrivateKeyStatus" role="status" aria-live="polite"></span>
                </div>
                ${translated('p', 'workspace_ssh_private_key_help', 'Use a dedicated, unencrypted automation key. It is encrypted before storage.', {
                    className: 'remote-connection-field-help',
                    id: 'sshPrivateKeyHelp',
                })}`,
        });
        const actions = renderer.renderActions({
            className: 'projects-create-buttons',
            buttons: [
                { id: 'cancelSshEditorBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                { id: 'testSshBtn', className: 'om-button border', key: 'workspace_ssh_test', fallback: 'Test connection' },
                { id: 'deleteSshBtn', className: 'om-button border danger', key: 'workspace_connections_remove', fallback: 'Remove', hidden: true },
                { id: 'saveSshBtn', className: 'om-button border submit', key: 'workspace_connections_save_changes', fallback: 'Save changes', type: 'submit' },
            ],
        });

        return [
            renderer.renderDescription({
                className: 'projects-create-description',
                titleClass: 'projects-create-description-title',
                title: { key: 'workspace_ssh_editor_description_title', fallback: 'Connect a device you control' },
                textClass: 'projects-create-description-text',
                paragraphs: [{
                    key: 'workspace_ssh_editor_description_text',
                    fallback: 'Use a dedicated SSH key and verify the host fingerprint before saving. This connection can power your personal ACP agents and terminal sessions.',
                }],
            }),
            renderRemoteIconField({
                pickerId: 'sshIconPicker',
                helpKey: 'workspace_ssh_icon_help',
                helpFallback: 'Choose the device type that makes this connection easy to recognize.',
            }),
            renderer.renderControlField({
                label: { key: 'workspace_connections_display_name', fallback: 'Display name' },
                control: { id: 'sshName', attributes: { required: true, maxlength: 120, autocomplete: 'off' } },
            }),
            renderRemoteGrid([
                renderer.renderControlField({
                    label: { key: 'workspace_ssh_host', fallback: 'Host' },
                    control: { id: 'sshHost', attributes: { required: true, maxlength: 255, autocomplete: 'off' } },
                }),
                renderer.renderControlField({
                    className: 'projects-create-input-group remote-connection-port-field',
                    label: { key: 'workspace_ssh_port', fallback: 'Port' },
                    control: { id: 'sshPort', type: 'number', value: '22', attributes: { min: 1, max: 65535, required: true } },
                }),
            ]),
            renderRemoteGrid([
                renderer.renderControlField({
                    label: { key: 'workspace_ssh_username', fallback: 'Username' },
                    control: { id: 'sshUsername', attributes: { required: true, maxlength: 128, autocomplete: 'username' } },
                }),
                renderer.renderControlField({
                    label: { key: 'workspace_ssh_workspace', fallback: 'Default remote workspace' },
                    control: {
                        id: 'sshWorkspace',
                        placeholder: '/Users/me/projects',
                        placeholderKey: 'workspace_ssh_workspace_placeholder',
                        attributes: { required: true, maxlength: 4096 },
                    },
                }),
            ]),
            hostKeyField,
            privateKeyField,
            renderRemoteEnabledToggle({
                id: 'sshEnabled',
                helpKey: 'workspace_ssh_enabled_help',
                helpFallback: 'Allow ACP profiles and terminal sessions to use this device.',
            }),
            renderRemoteDeleteConfirmation({
                kind: 'ssh',
                titleKey: 'workspace_ssh_delete_title',
                titleFallback: 'Remove this SSH device?',
                textKey: 'workspace_ssh_delete_text',
                textFallback: 'This cannot be undone. Remove dependent ACP agents first.',
            }),
            actions,
            '<p class="remote-connection-test-feedback" id="sshConnectionFeedback" role="status" aria-live="polite" hidden></p>',
        ].join('');
    }

    /** Build the ACP profile editor body. */
    function renderAcpEditorBody() {
        const permissionOptions = [
            ['ask', 'acp_permission_mode_ask', 'Ask the user'],
            ['deny', 'acp_permission_mode_deny', 'Deny automatically'],
            ['allow', 'acp_permission_mode_allow', 'Allow automatically'],
        ].map(([value, key, fallback]) => `<option value="${value}" data-i18n="${key}">${fallback}</option>`).join('');
        const advanced = `
            <details class="connections-advanced">
                ${translated('summary', 'workspace_acp_advanced', 'Advanced settings')}
                <div class="remote-connection-advanced-content">
                    ${renderer.renderControlField({
                        label: { key: 'workspace_acp_additional_dirs', fallback: 'Additional workspace directories (one per line)' },
                        control: { tag: 'textarea', id: 'acpAdditionalDirectories', attributes: { rows: 3 } },
                    })}
                    ${renderer.renderControlField({
                        label: { key: 'workspace_acp_mode', fallback: 'Agent mode' },
                        control: { id: 'acpMode', attributes: { maxlength: 128 } },
                    })}
                    ${renderRemoteGrid([
                        renderer.renderControlField({
                            label: { key: 'workspace_acp_permission_timeout', fallback: 'Permission timeout (seconds)' },
                            control: { id: 'acpPermissionTimeout', type: 'number', value: '300', attributes: { min: 15, max: 3600 } },
                        }),
                        renderer.renderControlField({
                            label: { key: 'workspace_acp_prompt_timeout', fallback: 'Prompt timeout (seconds)' },
                            control: { id: 'acpPromptTimeout', type: 'number', value: '1800', attributes: { min: 30, max: 7200 } },
                        }),
                    ])}
                </div>
            </details>`;
        const actions = renderer.renderActions({
            className: 'projects-create-buttons',
            buttons: [
                { id: 'cancelAcpEditorBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                { id: 'testAcpBtn', className: 'om-button border', key: 'workspace_acp_test', fallback: 'Test ACP agent', hidden: true },
                { id: 'deleteAcpBtn', className: 'om-button border danger', key: 'workspace_connections_remove', fallback: 'Remove', hidden: true },
                { id: 'saveAcpBtn', className: 'om-button border submit', key: 'workspace_connections_save_changes', fallback: 'Save changes', type: 'submit' },
            ],
        });

        return [
            renderer.renderDescription({
                className: 'projects-create-description',
                titleClass: 'projects-create-description-title',
                title: { key: 'workspace_acp_editor_description_title', fallback: 'Add a personal coding agent' },
                textClass: 'projects-create-description-text',
                paragraphs: [{
                    key: 'workspace_acp_editor_description_text',
                    fallback: 'Launch an ACP-compatible agent through one of your SSH devices and make it available in the model picker.',
                }],
            }),
            renderRemoteIconField({
                pickerId: 'acpIconPicker',
                helpKey: 'workspace_acp_icon_help',
                helpFallback: 'Choose an agent logo or paste safe custom SVG code.',
            }),
            renderer.renderControlField({
                label: { key: 'workspace_connections_display_name', fallback: 'Display name' },
                control: { id: 'acpName', attributes: { required: true, maxlength: 120, autocomplete: 'off' } },
            }),
            renderer.renderControlField({
                label: { key: 'workspace_acp_ssh_device', fallback: 'SSH device' },
                control: { tag: 'select', id: 'acpSsh', contentHtml: '', attributes: { required: true } },
            }),
            renderRemoteGrid([
                renderer.renderControlField({
                    label: { key: 'workspace_acp_executable', fallback: 'Remote executable' },
                    control: { id: 'acpExecutable', attributes: { required: true, maxlength: 1024 } },
                }),
                renderer.renderControlField({
                    label: { key: 'workspace_acp_permissions', fallback: 'Permissions' },
                    control: { tag: 'select', id: 'acpPermission', contentHtml: permissionOptions },
                }),
            ]),
            renderer.renderControlField({
                label: { key: 'workspace_acp_arguments', fallback: 'Arguments (one per line)' },
                control: { tag: 'textarea', id: 'acpArguments', attributes: { rows: 3 } },
            }),
            renderRemoteGrid([
                renderer.renderControlField({
                    label: { key: 'workspace_acp_workspace', fallback: 'Workspace root' },
                    control: { id: 'acpWorkspace', attributes: { required: true, maxlength: 4096 } },
                }),
                renderer.renderControlField({
                    label: { key: 'workspace_acp_cwd', fallback: 'Working directory' },
                    control: { id: 'acpCwd', value: '.', attributes: { required: true, maxlength: 4096 } },
                }),
            ]),
            advanced,
            renderRemoteEnabledToggle({
                id: 'acpEnabled',
                helpKey: 'workspace_acp_enabled_help',
                helpFallback: 'Show this agent in the model picker and allow new sessions.',
            }),
            renderRemoteDeleteConfirmation({
                kind: 'acp',
                titleKey: 'workspace_acp_delete_title',
                titleFallback: 'Remove this ACP agent?',
                textKey: 'workspace_acp_delete_text',
                textFallback: 'The personal model entry and its saved configuration will be deleted.',
            }),
            actions,
        ].join('');
    }

    renderer.mountPages({
        containerId: 'workspaceSectionSkills',
        pages: [
            {
                id: 'skillsContentCreate',
                title: { key: 'skills_create_title', fallback: 'Create a new skill' },
                bodyHtml: renderCreateSkillBody(),
                actions: {
                    className: 'projects-create-buttons',
                    buttons: [
                        { id: 'createSkillCancelBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                        { id: 'confirmCreateSkillBtn', className: 'om-button border submit', key: 'skills_create_confirm', fallback: 'Create skill' },
                    ],
                },
            },
            {
                id: 'skillsContentEdit',
                title: { key: 'skills_edit_title', fallback: 'Edit skill' },
                bodyHtml: renderEditSkillBody(),
                actions: {
                    className: 'projects-create-buttons',
                    buttons: [
                        { id: 'editSkillCancelBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                        { id: 'saveSkillChangesBtn', className: 'om-button border submit', key: 'skills_edit_save', fallback: 'Save changes' },
                    ],
                },
            },
        ],
    });

    renderer.mountPages({
        containerId: 'workspaceSectionPrompts',
        pages: [{
            id: 'promptLibraryEditorContent',
            titleId: 'promptEditorHeading',
            title: { key: 'prompt_editor_create_title', fallback: 'Create Prompt' },
            bodyHtml: renderPromptEditorBody(),
            actions: {
                className: 'projects-create-buttons',
                buttons: [
                    { id: 'promptEditorCancelBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                    { id: 'promptEditorSaveBtn', className: 'om-button border submit', key: 'prompt_editor_save_create', fallback: 'Create prompt' },
                ],
            },
        }],
    });

    renderer.mountPages({
        containerId: 'workspaceSectionConnections',
        pages: [
            {
                id: 'sshConnectionEditorPage',
                contentClass: 'projects-content remote-connection-editor-page',
                pageAttributes: { 'aria-hidden': 'true' },
                titleId: 'sshConnectionEditorTitle',
                title: { key: 'workspace_ssh_create_title', fallback: 'Add SSH device' },
                formTag: 'form',
                formId: 'sshConnectionForm',
                formClass: 'projects-create-form remote-connection-editor-form',
                bodyHtml: renderSshEditorBody(),
            },
            {
                id: 'acpConnectionEditorPage',
                contentClass: 'projects-content remote-connection-editor-page',
                pageAttributes: { 'aria-hidden': 'true' },
                titleId: 'acpConnectionEditorTitle',
                title: { key: 'workspace_acp_create_title', fallback: 'Add ACP agent' },
                formTag: 'form',
                formId: 'acpProfileForm',
                formClass: 'projects-create-form remote-connection-editor-form',
                bodyHtml: renderAcpEditorBody(),
            },
        ],
    });
})(window);
