/**
 * Dynamic create/edit page definitions for Automations and Projects.
 *
 * Both features use CreateEditFormRenderer for their page shell and common
 * controls. Mode-specific IDs and copy live in small configuration objects so
 * create/edit markup cannot drift as each feature evolves.
 */
(function mountWorkspaceCreateEditForms(global) {
    'use strict';

    const renderer = global.CreateEditFormRenderer;
    if (!renderer) {
        throw new Error('CreateEditFormRenderer must load before createEditForms.js');
    }

    /** Render a translated inline element with the shared renderer. */
    function translated(tag, key, fallback, options = {}) {
        return renderer.renderTranslatedElement({ tag, key, fallback, ...options });
    }

    /** Render a label containing translated primary text and an optional hint. */
    function renderLabelWithHint({ id, labelKey, labelFallback, hintKey, hintFallback }) {
        return `<label${renderer.renderAttributes({ id })}>` +
            `${translated('span', labelKey, labelFallback)} ` +
            `${translated('span', hintKey, hintFallback, { className: 'label-hint' })}` +
            '</label>';
    }

    /** Return the stable IDs and translations that differ by automation mode. */
    function getAutomationModeConfig(mode) {
        const isEdit = mode === 'edit';
        return {
            mode,
            isEdit,
            pageId: isEdit ? 'automationsContentEditAutomation' : 'automationsContentCreateAutomation',
            title: isEdit
                ? { key: 'automations_edit_title', fallback: 'Edit Automation' }
                : { key: 'automations_create_title', fallback: 'Create Automation' },
            idPrefix: isEdit ? 'automationEdit' : 'automation',
            nameInputId: isEdit ? 'automationEditNameInput' : 'automationNameInput',
            nameErrorId: isEdit ? 'automationEditNameError' : 'automationNameError',
            namePlaceholderKey: isEdit ? 'automations_edit_name_placeholder' : 'automations_create_name_placeholder',
            promptInputId: isEdit ? 'automationEditPromptInput' : 'automationPromptInput',
            promptErrorId: isEdit ? 'automationEditPromptError' : 'automationPromptError',
            promptPlaceholderKey: isEdit ? 'automations_edit_prompt_placeholder' : 'automations_create_prompt_placeholder',
            modelSelectId: isEdit ? 'automationEditModelSelect' : 'automationModelSelect',
            connectionsLabelId: isEdit ? 'automationConnectionsLabelEdit' : 'automationConnectionsLabelCreate',
            connectionsSelectId: isEdit ? 'automationEditConnectionsSelect' : 'automationConnectionsSelect',
            skillSelectId: isEdit ? 'automationEditSkillSelect' : 'automationSkillSelect',
            notesSelectId: isEdit ? 'automationEditNotesSelect' : 'automationNotesSelect',
            filesSelectedId: isEdit ? 'automationEditFilesSelected' : 'automationFilesSelected',
            fileInputId: isEdit ? 'automationEditFileInput' : 'automationFileInput',
            fileUploadId: isEdit ? 'automationEditFileUploadBtn' : 'automationFileUploadBtn',
            fileLibraryId: isEdit ? 'automationEditFileLibraryBtn' : 'automationFileLibraryBtn',
            fileDropdownId: isEdit ? 'automationEditFileLibraryDropdown' : 'automationFileLibraryDropdown',
            scheduleRulesId: isEdit ? 'automationEditScheduleRules' : 'automationScheduleRules',
            scheduleErrorId: isEdit ? 'automationEditScheduleError' : 'automationScheduleError',
            activeToggleId: isEdit ? 'automationEditActiveToggle' : 'automationActiveToggle',
            activeTitleId: isEdit ? 'automationEditActiveTitle' : 'automationActiveTitle',
            activeDescriptionId: isEdit ? 'automationEditActiveDescription' : 'automationActiveDescription',
        };
    }

    /** Render the file picker shared by create and edit automation modes. */
    function renderAutomationFilesField(config) {
        const labelKey = config.isEdit ? 'automations_edit_files_label' : 'automations_create_files_label';
        return renderer.renderField({
            className: 'projects-create-input-group',
            labelHtml: renderLabelWithHint({
                labelKey,
                labelFallback: 'Files',
                hintKey: 'automations_create_files_hint',
                hintFallback: '(optional attachments)',
            }),
            contentHtml: renderer.renderFilePicker({
                selectedId: config.filesSelectedId,
                inputId: config.fileInputId,
                uploadButtonId: config.fileUploadId,
                libraryButtonId: config.fileLibraryId,
                dropdownId: config.fileDropdownId,
                uploadLabel: { key: 'automations_files_upload', fallback: 'Upload files' },
                libraryLabel: { key: 'automations_files_choose_library', fallback: 'Choose from library' },
                uploadIconHtml: renderer.renderIcon('upload', { 'aria-hidden': 'true' }),
                libraryIconHtml: renderer.renderIcon('list', { 'aria-hidden': 'true' }),
            }),
        });
    }

    /** Render every field for one automation mode from shared mode metadata. */
    function renderAutomationBody(mode) {
        const config = getAutomationModeConfig(mode);
        const description = config.isEdit ? '' : renderer.renderDescription({
            className: 'projects-create-description',
            titleClass: 'projects-create-description-title',
            title: { key: 'automations_create_howto_title', fallback: 'How automations work' },
            textClass: 'projects-create-description-text',
            paragraphs: [{
                key: 'automations_create_howto_text',
                fallback: 'Create automations that automatically send prompts to an AI model. You can run them once at a specific date/time or on a recurring schedule.',
            }],
        });
        const nameField = renderer.renderNameIconField({
            groupClass: 'projects-create-input-group',
            label: { key: 'automations_create_name_label', fallback: 'Automation name' },
            iconPicker: {
                idPrefix: config.idPrefix,
                triggerTranslationKey: 'automations_icon_trigger',
                triggerFallback: 'Choose icon',
                typeTranslationKey: 'automations_icon_type_aria',
                typeFallback: 'Automation icon type',
            },
            input: {
                id: config.nameInputId,
                className: 'projects-create-input',
                placeholder: 'Enter automation name',
                placeholderKey: config.namePlaceholderKey,
            },
            error: {
                id: config.nameErrorId,
                className: 'field-validation-error',
                key: 'automations_error_name_required',
                fallback: 'Automation name is required',
                hidden: true,
            },
        });
        const promptField = renderer.renderField({
            className: 'projects-create-input-group',
            label: {
                key: 'automations_create_prompt_label',
                fallback: 'Automation prompt',
                attributes: { for: config.promptInputId },
            },
            contentHtml: `
                <textarea id="${config.promptInputId}" class="projects-create-textarea automations-create-textarea"
                    placeholder="Enter the prompt to send when this automation runs..."
                    data-i18n-attr="placeholder:${config.promptPlaceholderKey}" rows="4"
                    aria-describedby="${config.promptErrorId}" aria-invalid="false"></textarea>
                ${translated('p', 'automations_error_prompt_required', 'Automation prompt is required', {
                    className: 'field-validation-error',
                    id: config.promptErrorId,
                    attributes: { 'aria-hidden': 'true', hidden: true },
                })}`,
        });
        const modelField = renderer.renderField({
            className: 'projects-create-input-group',
            label: { key: 'automations_create_model_label', fallback: 'AI Model' },
            contentHtml: `<div class="shared-model-select" id="${config.modelSelectId}"></div>`,
        });
        const connectionsField = renderer.renderField({
            className: 'projects-create-input-group',
            labelHtml: renderLabelWithHint({
                id: config.connectionsLabelId,
                labelKey: 'automations_create_connections_label',
                labelFallback: 'Connections',
                hintKey: 'automations_create_connections_hint',
                hintFallback: '(optional tools for this automation)',
            }),
            contentHtml: `<div class="automations-connections-select" id="${config.connectionsSelectId}"
                role="group" aria-labelledby="${config.connectionsLabelId}"></div>`,
        });
        const skillField = renderer.renderField({
            className: 'projects-create-input-group',
            labelHtml: renderLabelWithHint({
                labelKey: 'automations_create_skill_label',
                labelFallback: 'Skill',
                hintKey: 'automations_create_skill_hint',
                hintFallback: '(optional context for the AI)',
            }),
            contentHtml: `<div class="shared-skill-select" id="${config.skillSelectId}"></div>`,
        });
        const notesField = renderer.renderField({
            className: 'projects-create-input-group',
            labelHtml: renderLabelWithHint({
                labelKey: 'automations_create_notes_label',
                labelFallback: 'Notes',
                hintKey: 'automations_create_notes_hint',
                hintFallback: '(optional reference materials)',
            }),
            contentHtml: `<div class="automations-notes-select" id="${config.notesSelectId}"></div>`,
        });
        const scheduleField = renderer.renderField({
            className: 'projects-create-input-group',
            labelHtml: renderLabelWithHint({
                labelKey: 'automations_create_schedule_label',
                labelFallback: 'Schedule',
                hintKey: 'automations_create_schedule_hint',
                hintFallback: '(when should this automation run?)',
            }),
            contentHtml: `
                <div id="${config.scheduleRulesId}" aria-describedby="${config.scheduleErrorId}" aria-invalid="false"></div>
                ${translated('p', 'automations_schedule_error_configure', 'Please configure a schedule', {
                    className: 'field-validation-error',
                    id: config.scheduleErrorId,
                    attributes: { 'aria-hidden': 'true', hidden: true },
                })}`,
        });
        const activeToggle = `
            <div class="automations-active-toggle">
                <div class="automations-active-toggle-label">
                    ${translated('span', 'automations_create_active_title', 'Automation Active', {
                        className: 'automations-active-toggle-title',
                        id: config.activeTitleId,
                    })}
                    ${translated('span', 'automations_create_active_desc', 'Enable to start running on schedule', {
                        className: 'automations-active-toggle-desc',
                        id: config.activeDescriptionId,
                    })}
                </div>
                <label class="toggle-switch">
                    <input type="checkbox" id="${config.activeToggleId}" class="toggle-input" checked
                        aria-labelledby="${config.activeTitleId}" aria-describedby="${config.activeDescriptionId}">
                    <span class="toggle-slider"></span>
                </label>
            </div>`;

        return [
            description,
            nameField,
            promptField,
            modelField,
            connectionsField,
            skillField,
            notesField,
            renderAutomationFilesField(config),
            scheduleField,
            activeToggle,
        ].join('');
    }

    /** Build one complete automation page configuration. */
    function createAutomationPage(mode) {
        const config = getAutomationModeConfig(mode);
        return {
            id: config.pageId,
            contentClass: 'projects-content',
            headerClass: 'projects-header',
            title: config.title,
            formClass: 'projects-create-form',
            bodyHtml: renderAutomationBody(mode),
            actions: {
                className: 'projects-create-buttons',
                buttons: config.isEdit ? [
                    { id: 'editAutomationCancelBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                    { id: 'saveAutomationChangesBtn', className: 'om-button border submit', key: 'automations_edit_save', fallback: 'Save changes' },
                ] : [
                    { id: 'createAutomationCancelBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                    { id: 'confirmCreateAutomationBtn', className: 'om-button border submit', key: 'automations_create_confirm', fallback: 'Create automation' },
                ],
            },
        };
    }

    /** Return the stable IDs and translations that differ by project mode. */
    function getProjectModeConfig(mode) {
        const isEdit = mode === 'edit';
        return {
            mode,
            isEdit,
            pageId: isEdit ? 'projectsContentEditProject' : 'projectsContentCreateProject',
            title: isEdit
                ? { key: 'projects_edit_title', fallback: 'Edit project' }
                : { key: 'projects_create_title', fallback: 'Create a personal project' },
            idPrefix: isEdit ? 'projectEdit' : 'project',
            nameInputId: isEdit ? 'projectEditNameInput' : 'projectNameInput',
            nameErrorId: isEdit ? 'projectEditNameError' : 'projectNameError',
            namePlaceholderKey: isEdit ? 'projects_edit_name_placeholder' : 'projects_create_name_placeholder',
            namePlaceholder: isEdit ? 'Enter new project name' : 'Enter project name',
            nameErrorKey: isEdit ? 'projects_edit_name_error' : 'projects_create_name_error',
            instructionInputId: isEdit ? 'projectEditInstructionInput' : 'projectInstructionInput',
            memoryCardId: isEdit ? 'projectEditSeparateMemoryCard' : 'projectSeparateMemoryCard',
            memoryToggleId: isEdit ? 'projectEditSeparateMemoryToggle' : 'projectSeparateMemoryToggle',
            memoryLabelId: isEdit ? 'projectEditSeparateMemoryToggleLabel' : 'projectSeparateMemoryToggleLabel',
            memoryDescriptionId: isEdit ? 'projectEditSeparateMemoryToggleDescription' : 'projectSeparateMemoryToggleDescription',
            memoryDescriptionKey: isEdit ? 'projects_separate_memory_toggle_desc_edit' : 'projects_separate_memory_toggle_desc_create',
            memoryDescriptionFallback: isEdit
                ? 'All chats in this project use one shared project memory that every member can manage.'
                : "When enabled, chats in this project use a shared project memory instead of each member's personal memory.",
        };
    }

    /** Render every field for one project mode from shared mode metadata. */
    function renderProjectBody(mode) {
        const config = getProjectModeConfig(mode);
        const description = renderer.renderDescription({
            className: 'projects-create-description',
            titleClass: 'projects-create-description-title',
            title: config.isEdit
                ? { key: 'projects_edit_description_title', fallback: 'Update your project name' }
                : { key: 'projects_create_howto_title', fallback: 'How to use projects' },
            textClass: 'projects-create-description-text',
            paragraphs: config.isEdit ? [{
                key: 'projects_edit_description_text',
                fallback: 'Choose a name that helps you recognize the project later. You can change it again whenever you need.',
            }] : [{
                key: 'projects_create_howto_text1',
                fallback: 'Projects help organize your work and leverage knowledge across multiple conversations. Upload docs, code, and files to create themed collections that the AI model can reference again and again.',
            }, {
                key: 'projects_create_howto_text2',
                fallback: 'Start by creating a memorable title and description to organize your project. You can always edit it later.',
            }],
        });
        const nameField = renderer.renderNameIconField({
            groupClass: 'projects-create-input-group',
            label: { key: 'projects_create_name_label', fallback: 'Project name' },
            iconPicker: {
                idPrefix: config.idPrefix,
                triggerTranslationKey: 'projects_edit_icon_trigger',
                triggerFallback: 'Choose icon',
                typeTranslationKey: 'projects_icon_type_aria',
                typeFallback: 'Project icon type',
            },
            input: {
                id: config.nameInputId,
                className: 'projects-create-input',
                placeholder: config.namePlaceholder,
                placeholderKey: config.namePlaceholderKey,
            },
            error: {
                id: config.nameErrorId,
                className: 'skills-input-error',
                key: config.nameErrorKey,
                fallback: 'Please enter a project name',
                hidden: false,
            },
        });
        const instructionField = renderer.renderField({
            className: 'projects-create-input-group',
            label: {
                key: 'projects_edit_instruction_label',
                fallback: 'System instruction',
                attributes: { for: config.instructionInputId },
            },
            contentHtml: `<textarea id="${config.instructionInputId}" class="projects-create-textarea"
                placeholder="Describe the guidance for this project"
                data-i18n-attr="placeholder:projects_edit_instruction_placeholder" rows="5"></textarea>`,
        });
        const memoryCard = `
            <label class="memories-card projects-memory-toggle-card" id="${config.memoryCardId}" for="${config.memoryToggleId}" hidden>
                <span class="memories-setting-row">
                    <span>
                        ${translated('span', 'projects_separate_memory_toggle_label', 'Use separate shared project memory', {
                            id: config.memoryLabelId,
                        })}
                        ${translated('small', config.memoryDescriptionKey, config.memoryDescriptionFallback, {
                            id: config.memoryDescriptionId,
                        })}
                    </span>
                    <input type="checkbox" id="${config.memoryToggleId}" aria-describedby="${config.memoryDescriptionId}" aria-labelledby="${config.memoryLabelId}">
                </span>
            </label>`;
        return [description, nameField, instructionField, memoryCard].join('');
    }

    /** Build one complete project page configuration. */
    function createProjectPage(mode) {
        const config = getProjectModeConfig(mode);
        return {
            id: config.pageId,
            contentClass: 'projects-content',
            headerClass: 'projects-header',
            title: config.title,
            formClass: 'projects-create-form',
            bodyHtml: renderProjectBody(mode),
            actions: {
                className: 'projects-create-buttons',
                buttons: config.isEdit ? [
                    { id: 'editProjectCancelBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                    { id: 'projectManageMemoryBtn', className: 'om-button border', key: 'projects_manage_memory_button', fallback: 'Manage project memory', hidden: true },
                    { id: 'saveProjectChangesBtn', className: 'om-button border submit', key: 'projects_edit_save', fallback: 'Save changes' },
                ] : [
                    { id: 'createProjectCancelBtn', className: 'om-button border', key: 'common_cancel', fallback: 'Cancel' },
                    { id: 'confirmCreateProjectBtn', className: 'om-button border submit', key: 'projects_create_confirm', fallback: 'Create project' },
                ],
            },
        };
    }

    // Mount synchronously. Both behavior scripts are deferred after this file
    // and can therefore keep simple, stable DOM references at module startup.
    renderer.mountPages({
        containerId: 'automationsContainer',
        pages: [createAutomationPage('create'), createAutomationPage('edit')],
    });
    renderer.mountPages({
        containerId: 'projectsContainer',
        pages: [createProjectPage('create'), createProjectPage('edit')],
    });
})(window);
