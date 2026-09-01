(function () {
    const desc = (id, i18n, text, attrs) => ({ id, i18n, text, attrs });
    const title = (id, i18n, text) => ({ id, i18n, text });
    const cancel = (id, i18n = 'btn_cancel', text = 'Cancel') => ({ id, role: 'cancel', variant: 'cancel', i18n, text });
    const danger = (id, i18n, text, textId, options = {}) => ({ id, variant: 'danger', i18n, text, textId, ...options });
    const submit = (id, i18n, text, textId, options = {}) => ({ id, variant: 'submit', i18n, text, textId, ...options });
    const warningSvg = Icons.resolveIcon("warning");
    const addUserSvg = Icons.resolveIcon("removeUser");
    const disableSvg = Icons.resolveIcon("error");
    const importCard = ({ id, titleId, titleI18n, titleText, subtitleId, subtitleI18n, subtitleText, closeId, closeI18nAttr = 'modal_close_import_aria', closeAria = 'Close import dialog', bodyHtml, actions, cardClass = '' }) => {
        const subtitleAttrs = [
            subtitleId ? `id="${subtitleId}"` : '',
            subtitleI18n ? `data-i18n="${subtitleI18n}"` : '',
        ].filter(Boolean).join(' ');

        return {
            id,
            cardClass: ['delete-warning-card--import', 'shared-modal--wide', cardClass].filter(Boolean).join(' '),
            role: 'dialog',
            ariaModal: 'true',
            ariaLabelledby: titleId,
            ariaDescribedby: subtitleId,
            contentHtml: `
                <header class="provider-import-header shared-modal-header shared-modal-header--main">
                    <div class="shared-modal-heading">
                        <h2 class="provider-import-title shared-modal-title" id="${titleId}" data-i18n="${titleI18n}">${titleText}</h2>
                        ${subtitleText ? `<p class="provider-import-subtitle shared-modal-subtitle" ${subtitleAttrs}>${subtitleText}</p>` : ''}
                    </div>
                    ${closeId ? `<button type="button" class="provider-import-close shared-modal-close" id="${closeId}" data-i18n-attr="aria-label:${closeI18nAttr}" aria-label="${closeAria}">${window.Icons?.close || ''}</button>` : ''}
                </header>
                <div class="provider-import-shared-body shared-modal-body">${bodyHtml}</div>
            `,
            actions,
        };
    };
    const statsCard = ({ id, titleId, titleI18n, titleText, iconHtml, cardClass = '', bodyHtml, actions }) => ({
        id,
        cardClass: ['user-stats-modal', cardClass].filter(Boolean).join(' '),
        role: 'dialog',
        ariaModal: 'true',
        ariaLabelledby: titleId,
        overlayAttrs: { 'aria-hidden': 'true' },
        contentHtml: `
            <header class="user-stats-modal-header shared-modal-header shared-modal-header--main">
                <div class="user-stats-modal-heading shared-modal-heading">
                    <div class="user-stats-modal-icon">${iconHtml}</div>
                    <h3 class="user-stats-modal-title shared-modal-title" id="${titleId}" data-i18n="${titleI18n}">${titleText}</h3>
                </div>
            </header>
            <div class="user-stats-modal-body shared-modal-body">
                ${bodyHtml}
            </div>
        `,
        actions,
    });

    window.DeleteWarningModal?.mountAll([
        statsCard({
            id: 'securityIpsStatsRegulatoryModal',
            titleId: 'securityIpsStatsRegulatoryTitle',
            titleI18n: 'security_ips_stats_regulatory_title',
            titleText: 'IP analytics compliance',
            iconHtml: warningSvg,
            bodyHtml: `
                <p data-i18n="security_ips_stats_regulatory_intro">Before enabling IP origin analytics, please confirm that you have:</p>
                <ul>
                    <li data-i18n="security_ips_stats_regulatory_item1">verified that IP-based geolocation and abuse monitoring are allowed under your legal basis and privacy documentation</li>
                    <li data-i18n="security_ips_stats_regulatory_item2">limited access to these analytics to authorized administrators and security personnel</li>
                    <li data-i18n="security_ips_stats_regulatory_item3">defined retention and deletion rules for the resulting security telemetry</li>
                    <li data-i18n="security_ips_stats_regulatory_item4">checked whether cross-border geolocation providers are acceptable in your environment</li>
                </ul>
                <div class="user-stats-modal-checkbox">
                    <input type="checkbox" id="securityIpsStatsRegulatoryCheckbox">
                    <label for="securityIpsStatsRegulatoryCheckbox" data-i18n="security_ips_stats_regulatory_confirm">I confirm that enabling IP origin analytics complies with all applicable regulations, notices, and internal security policies.</label>
                </div>
                <div class="form-group">
                    <label class="form-label" for="securityIpsStatsRegulatoryDocumentation" data-i18n="security_ips_stats_regulatory_documentation_label">Legal basis or policy reference</label>
                    <textarea id="securityIpsStatsRegulatoryDocumentation" class="form-input" rows="3" data-i18n-attr="placeholder:security_ips_stats_regulatory_documentation_placeholder" placeholder="Legal basis, privacy notice, or internal policy reference"></textarea>
                    <p class="settings-row-desc" data-i18n="security_ips_stats_regulatory_documentation_desc">Document the legal basis, privacy notice reference, or internal policy that authorizes this telemetry.</p>
                </div>
            `,
            actions: [cancel('securityIpsStatsRegulatoryCancelBtn', 'btn_cancel', 'Cancel'), submit('securityIpsStatsRegulatoryConfirmBtn', 'security_ips_stats_enable_btn', 'Enable Analytics', null, { disabled: true })],
        }),
        statsCard({
            id: 'securityIpsStatsDisableModal',
            titleId: 'securityIpsStatsDisableTitle',
            titleI18n: 'security_ips_stats_disable_title',
            titleText: 'Disable IP origin analytics',
            iconHtml: disableSvg,
            cardClass: 'user-stats-disable-modal',
            bodyHtml: '<p data-i18n="security_ips_stats_disable_desc">Are you sure you want to disable IP origin analytics? New country and abuse events will no longer be recorded. Existing analytics data will remain available until you remove it separately.</p>',
            actions: [cancel('securityIpsStatsDisableCancelBtn', 'btn_cancel', 'Cancel'), danger('securityIpsStatsDisableConfirmBtn', 'security_ips_stats_disable_btn', 'Disable Analytics')],
        }),
        statsCard({
            id: 'userStatsRegulatoryModal',
            titleId: 'userStatsRegulatoryTitle',
            titleI18n: 'user_stats_regulatory_title',
            titleText: 'Regulatory Compliance',
            iconHtml: warningSvg,
            bodyHtml: `
                <p data-i18n="user_stats_regulatory_intro">Before enabling user-based statistics tracking, please confirm that you have:</p>
                <ul>
                    <li data-i18n="user_stats_regulatory_item1">Reviewed applicable data protection regulations (GDPR, CCPA, etc.)</li>
                    <li data-i18n="user_stats_regulatory_item2">Obtained necessary consent from users if required</li>
                    <li data-i18n="user_stats_regulatory_item3">Updated your privacy policy to reflect this data collection</li>
                    <li data-i18n="user_stats_regulatory_item4">Established appropriate data retention and deletion policies</li>
                </ul>
                <div class="user-stats-modal-checkbox">
                    <input type="checkbox" id="userStatsRegulatoryCheckbox">
                    <label for="userStatsRegulatoryCheckbox" data-i18n="user_stats_regulatory_confirm">I confirm that enabling user-based statistics complies with all applicable regulations and organizational policies.</label>
                </div>
            `,
            actions: [cancel('userStatsRegulatoryCancelBtn', 'btn_cancel', 'Cancel'), submit('userStatsRegulatoryConfirmBtn', 'user_stats_enable_btn', 'Enable Tracking', null, { disabled: true })],
        }),
        statsCard({
            id: 'userStatsAddUserModal',
            titleId: 'userStatsAddUserTitle',
            titleI18n: 'user_stats_add_user_title',
            titleText: 'Add User to Tracking',
            iconHtml: addUserSvg,
            cardClass: 'user-stats-add-modal',
            bodyHtml: `
                <div class="user-stats-user-search">
                    <input type="text" id="userStatsAddUserSearch" data-i18n-attr="placeholder:user_stats_search_placeholder" placeholder="Search users by email or name...">
                </div>
                <div class="user-stats-user-search-results" id="userStatsAddUserResults"></div>
            `,
            actions: [cancel('userStatsAddUserCancelBtn', 'btn_cancel', 'Cancel'), submit('userStatsAddUserConfirmBtn', 'user_stats_add_btn', 'Add User', null, { disabled: true })],
        }),
        statsCard({
            id: 'userStatsDisableModal',
            titleId: 'userStatsDisableTitle',
            titleI18n: 'user_stats_disable_title',
            titleText: 'Disable User Tracking',
            iconHtml: disableSvg,
            cardClass: 'user-stats-disable-modal',
            bodyHtml: '<p data-i18n="user_stats_disable_desc">Are you sure you want to disable user-based statistics tracking? New statistics will no longer be associated with users. Existing user statistics data will be preserved.</p>',
            actions: [cancel('userStatsDisableCancelBtn', 'btn_cancel', 'Cancel'), danger('userStatsDisableConfirmBtn', 'user_stats_disable_btn', 'Disable Tracking')],
        }),
        importCard({
            id: 'userBulkOptionsOverlay',
            cardClass: 'user-bulk-options-modal',
            titleId: 'userBulkOptionsTitle',
            titleI18n: 'user_bulk_options_modal_title',
            titleText: 'Set import password',
            subtitleId: 'userBulkOptionsSubtitle',
            subtitleI18n: 'user_bulk_options_modal_subtitle',
            subtitleText: 'Choose the default password and password-change requirement before creating users from this file.',
            closeId: 'userBulkOptionsClose',
            bodyHtml: `
                <div class="provider-import-controls">
                    <div class="provider-import-file" id="userBulkOptionsFileName"></div>
                </div>
                <div class="provider-import-options user-bulk-modal-options" aria-labelledby="userBulkOptionsPasswordTitle">
                    <div class="provider-import-option-field">
                        <label class="form-label" for="userBulkDefaultPassword" id="userBulkOptionsPasswordTitle" data-i18n="users_import_default_password_label">Default password</label>
                        <input type="password" class="form-input" id="userBulkDefaultPassword" autocomplete="new-password" required data-i18n-attr="placeholder:users_import_default_password_placeholder" placeholder="Enter a temporary password">
                        <p class="provider-import-option-desc" data-i18n="user_bulk_default_password_desc">A unique temporary password will be generated from this value for each imported user.</p>
                    </div>
                    <label class="provider-import-option-toggle">
                        <div class="provider-import-option-toggle-text">
                            <span class="provider-import-option-title" data-i18n="users_import_force_password_change_label">Force password change</span>
                            <span class="provider-import-option-desc" data-i18n="users_import_force_password_change_desc">Require imported users to set a new password after sign-in.</span>
                        </div>
                        <span class="toggle-switch">
                            <input type="checkbox" id="userBulkForcePasswordChange" class="toggle-input" checked data-i18n-attr="aria-label:users_import_force_password_change_label" aria-label="Force password change">
                            <span class="toggle-slider"></span>
                        </span>
                    </label>
                </div>
                <div class="provider-import-status" id="userBulkOptionsStatus" role="alert" hidden></div>
            `,
            actions: [cancel('userBulkOptionsCancel'), submit('userBulkOptionsConfirm', 'users_import_btn', 'Import Users')],
        }),
        importCard({
            id: 'groupImportOverlay',
            titleId: 'groupImportTitle',
            titleI18n: 'modal_import_groups_title',
            titleText: 'Import Groups',
            subtitleId: 'groupImportSubtitle',
            subtitleI18n: 'modal_import_groups_subtitle',
            subtitleText: 'Select which groups from the uploaded file should be created.',
            closeId: 'groupImportClose',
            closeI18nAttr: 'modal_close_aria',
            closeAria: 'Close modal',
            bodyHtml: `
                <div class="provider-import-body group-import-body">
                    <div class="group-import-source">
                        <span class="group-import-source-icon" aria-hidden="true">${window.Icons?.file || ''}</span>
                        <span class="group-import-source-name" id="groupImportFileName"></span>
                        <button type="button" class="om-button border cancel group-import-source-btn" id="groupImportChooseFile"><span data-i18n="modal_choose_file">Choose file</span></button>
                        <input type="file" id="groupImportHiddenFileInput" accept=".json,application/json" hidden>
                    </div>
                    <div id="groupImportStatus" class="provider-import-status" hidden></div>
                    <div class="group-import-list-head">
                        <label class="checkbox-row group-import-select-all">
                            <input type="checkbox" id="groupImportSelectAll">
                            <span data-i18n="modal_select_all">Select all</span>
                        </label>
                        <span class="group-import-count" id="groupImportCount" aria-live="polite"></span>
                    </div>
                    <div id="groupImportList" class="provider-import-list group-import-list" role="listbox" aria-multiselectable="true"></div>
                </div>
            `,
            actions: [cancel('groupImportCancel'), submit('groupImportConfirm', 'modal_import_selected', 'Import Selected', null, { disabled: true })],
        }),
        importCard({
            id: 'importProvidersOverlay',
            titleId: 'importProvidersTitle',
            titleI18n: 'modal_import_providers_title',
            titleText: 'Import Providers',
            subtitleId: 'importProvidersSubtitle',
            subtitleI18n: 'modal_import_providers_subtitle',
            subtitleText: 'Select the providers you want to import from this file.',
            closeId: 'importProvidersClose',
            bodyHtml: `
                <div class="provider-import-controls">
                    <label class="provider-import-select-all"><input type="checkbox" id="importProvidersSelectAll" checked><span data-i18n="modal_select_all">Select all</span></label>
                    <div class="provider-import-file" id="importProvidersFileName"></div>
                </div>
                <p class="provider-import-description" data-i18n="providers_import_credentials_notice">API keys are not included in provider exports. Enter fresh keys for providers that require them.</p>
                <div class="provider-import-list" id="importProvidersList" role="listbox" aria-multiselectable="true"></div>
                <div class="provider-import-status" id="importProvidersStatus" role="alert" hidden></div>
            `,
            actions: [cancel('importProvidersCancel'), submit('importProvidersConfirm', 'modal_import_selected', 'Import Selected')],
        }),
        importCard({
            id: 'importWebsearchProvidersOverlay',
            titleId: 'importWebsearchProvidersTitle',
            titleI18n: 'modal_import_providers_title',
            titleText: 'Import Providers',
            subtitleId: 'importWebsearchProvidersSubtitle',
            subtitleI18n: 'modal_import_providers_subtitle',
            subtitleText: 'Select the providers you want to import from this file.',
            closeId: 'importWebsearchProvidersClose',
            bodyHtml: `
                <div class="provider-import-controls">
                    <label class="provider-import-select-all"><input type="checkbox" id="importWebsearchProvidersSelectAll" checked><span data-i18n="modal_select_all">Select all</span></label>
                    <div class="provider-import-file" id="importWebsearchProvidersFileName"></div>
                </div>
                <div class="provider-import-list" id="importWebsearchProvidersList" role="listbox" aria-multiselectable="true"></div>
                <div class="provider-import-status" id="importWebsearchProvidersStatus" role="alert" hidden></div>
            `,
            actions: [cancel('importWebsearchProvidersCancel'), submit('importWebsearchProvidersConfirm', 'modal_import_selected', 'Import Selected')],
        }),
        importCard({
            id: 'importModelsOverlay',
            titleId: 'importModelsTitle',
            titleI18n: 'modal_import_models_title',
            titleText: 'Import Models',
            subtitleId: 'importModelsSubtitle',
            subtitleI18n: 'modal_import_models_subtitle',
            subtitleText: 'Select the models you want to import from this file.',
            closeId: 'importModelsClose',
            bodyHtml: `
                <div class="provider-import-controls">
                    <label class="provider-import-select-all"><input type="checkbox" id="importModelsSelectAll" checked><span data-i18n="modal_select_all">Select all</span></label>
                    <div class="provider-import-file" id="importModelsFileName"></div>
                </div>
                <div class="provider-import-list" id="importModelsList" role="listbox" aria-multiselectable="true"></div>
                <div class="provider-import-status" id="importModelsStatus" role="alert" hidden></div>
            `,
            actions: [cancel('importModelsCancel'), submit('importModelsConfirm', 'modal_import_selected', 'Import Selected')],
        }),
        importCard({
            id: 'userExportJobsOverlay',
            cardClass: 'user-export-jobs-modal',
            titleId: 'userExportJobsTitle',
            titleI18n: 'users_export_jobs_title',
            titleText: 'Export Jobs',
            subtitleId: 'userExportJobsSubtitle',
            subtitleI18n: 'users_export_jobs_desc',
            subtitleText: 'Queued exports continue in the background. Return here to download finished ZIP files.',
            closeId: 'userExportJobsClose',
            closeI18nAttr: 'modal_close_dialog_aria',
            closeAria: 'Close dialog',
            bodyHtml: `
                <div class="provider-import-controls user-export-jobs-controls">
                    <div class="provider-import-file"><p data-i18n="users_export_jobs_privacy_note">Exports can contain sensitive personal data. Delete generated ZIP files when you no longer need them.</p></div>
                    <div class="provider-import-option-field">
                        <label class="form-label" for="userExportReason" data-i18n="reason_label">Reason</label>
                        <input type="text" class="form-input" id="userExportReason" minlength="3" maxlength="255" required autocomplete="off" aria-describedby="userExportReasonDescription" data-i18n-attr="placeholder:enter_reason_placeholder" placeholder="Enter a reason">
                        <p class="provider-import-option-desc" id="userExportReasonDescription" data-i18n="users_export_reason_desc">Explain why this sensitive user archive is required. The reason is recorded in the audit log.</p>
                    </div>
                    <div class="user-export-jobs-toolbar">
                        <button type="button" class="om-button border submit" id="createUserExportJobButton"><span data-i18n="users_export_create_job_btn">Create Export</span></button>
                        <button type="button" class="om-button border cancel" id="refreshUserExportJobsButton"><span data-i18n="users_export_jobs_refresh_btn">Refresh</span></button>
                    </div>
                </div>
                <div class="provider-import-list user-export-jobs-list" id="userExportJobsList" aria-live="polite" data-i18n-attr="aria-label:users_export_jobs_list_aria" aria-label="User export jobs"></div>
                <div class="provider-import-status" id="userExportJobsStatus" role="alert" hidden></div>
            `,
            actions: [cancel('userExportJobsCancel', 'btn_close', 'Close')],
        }),
        importCard({
            id: 'importUsersOverlay',
            titleId: 'importUsersTitle',
            titleI18n: 'modal_import_users_title',
            titleText: 'Import Users',
            subtitleId: 'importUsersSubtitle',
            subtitleI18n: 'modal_import_users_subtitle',
            subtitleText: 'Select the users you want to import from this file.',
            closeId: 'importUsersClose',
            bodyHtml: `
                <div class="provider-import-controls">
                    <label class="provider-import-select-all"><input type="checkbox" id="importUsersSelectAll" checked><span data-i18n="modal_select_all">Select all</span></label>
                    <div class="provider-import-file" id="importUsersFileName"></div>
                </div>
                <div class="provider-import-list" id="importUsersList" role="listbox" aria-multiselectable="true"></div>
                <div class="provider-import-options" aria-labelledby="importUsersPasswordTitle">
                    <div class="provider-import-option-field">
                        <label class="form-label" for="importUsersDefaultPassword" id="importUsersPasswordTitle" data-i18n="users_import_default_password_label">Default password</label>
                        <input type="password" class="form-input" id="importUsersDefaultPassword" autocomplete="new-password" required data-i18n-attr="placeholder:users_import_default_password_placeholder" placeholder="Enter a temporary password">
                        <p class="provider-import-option-desc" data-i18n="users_import_default_password_desc">Imported users will sign in with this password.</p>
                    </div>
                    <label class="provider-import-option-toggle">
                        <div class="provider-import-option-toggle-text"><span class="provider-import-option-title" data-i18n="users_import_force_password_change_label">Force password change</span><span class="provider-import-option-desc" data-i18n="users_import_force_password_change_desc">Require imported users to set a new password after sign-in.</span></div>
                        <span class="toggle-switch"><input type="checkbox" id="importUsersForcePasswordChange" class="toggle-input" checked data-i18n-attr="aria-label:users_import_force_password_change_label" aria-label="Force password change"><span class="toggle-slider"></span></span>
                    </label>
                </div>
                <div class="provider-import-status" id="importUsersStatus" role="alert" hidden></div>
            `,
            actions: [cancel('importUsersCancel'), submit('importUsersConfirm', 'modal_import_selected', 'Import Selected')],
        }),
        importCard({
            id: 'importCustomPythonToolsOverlay',
            titleId: 'importCustomPythonToolsTitle',
            titleI18n: 'custom_tools_import_title',
            titleText: 'Import Custom Python Tools',
            subtitleI18n: 'custom_tools_import_subtitle',
            subtitleText: 'Select the custom Python tools you want to import from this file.',
            closeId: 'importCustomPythonToolsClose',
            bodyHtml: `
                <div class="provider-import-controls"><label class="provider-import-select-all"><input type="checkbox" id="importCustomPythonToolsSelectAll" checked><span data-i18n="modal_select_all">Select all</span></label><div class="provider-import-file" id="importCustomPythonToolsFileName"></div></div>
                <div class="provider-import-list" id="importCustomPythonToolsList" role="listbox" aria-multiselectable="true"></div>
                <div class="provider-import-status" id="importCustomPythonToolsStatus" role="alert" hidden></div>
            `,
            actions: [cancel('importCustomPythonToolsCancel'), submit('importCustomPythonToolsConfirm', 'modal_import_selected', 'Import Selected')],
        }),
        importCard({
            id: 'importAdminSkillsOverlay',
            titleId: 'importAdminSkillsTitle',
            titleI18n: 'admin_skills_import_title',
            titleText: 'Import Managed Skills',
            subtitleId: 'importAdminSkillsSubtitle',
            subtitleI18n: 'admin_skills_import_subtitle',
            subtitleText: 'Upload Agent Skills packages or paste SKILL.md Markdown.',
            closeId: 'importAdminSkillsClose',
            bodyHtml: `
                <div class="admin-skill-import-body">
                    <div class="admin-skill-import-tabs" role="tablist" aria-label="Import source" data-i18n-attr="aria-label:admin_skills_import_source_tabs_aria">
                        <button type="button" class="admin-skill-import-tab active" id="importAdminSkillsTabFiles" role="tab" aria-selected="true" aria-controls="importAdminSkillsPanelFiles">
                            ${window.Icons?.archive || ''}<span data-i18n="admin_skills_import_tab_files">Upload files</span>
                        </button>
                        <button type="button" class="admin-skill-import-tab" id="importAdminSkillsTabPaste" role="tab" aria-selected="false" aria-controls="importAdminSkillsPanelPaste">
                            ${window.Icons?.file || ''}<span data-i18n="admin_skills_import_tab_paste">Paste Markdown</span>
                        </button>
                    </div>
                    <div class="admin-skill-import-panel" id="importAdminSkillsPanelFiles" role="tabpanel" aria-labelledby="importAdminSkillsTabFiles">
                        <div class="admin-skill-import-dropzone" id="importAdminSkillsDropzone" data-i18n-attr="aria-label:admin_skills_import_dropzone_aria" aria-label="Choose or drop Markdown and ZIP skill files">
                            <input type="file" id="importAdminSkillsFileInput" accept=".md,.zip,text/markdown,text/plain,application/zip" multiple hidden>
                            <div class="admin-skill-import-dropzone-icon">${window.Icons?.archive || ''}</div>
                            <p class="admin-skill-import-dropzone-title" data-i18n="admin_skills_import_dropzone_title">Drop .md or .zip skill files here</p>
                            <p class="admin-skill-import-dropzone-hint"><span data-i18n="admin_skills_import_dropzone_or">or</span> <button type="button" class="admin-skill-import-browse" id="importAdminSkillsBrowse" data-i18n="admin_skills_import_browse">browse files</button></p>
                            <p class="admin-skill-import-dropzone-formats" data-i18n="admin_skills_import_dropzone_formats">Multiple Markdown files and Agent Skills ZIP packages are supported.</p>
                        </div>
                    </div>
                    <div class="admin-skill-import-panel" id="importAdminSkillsPanelPaste" role="tabpanel" aria-labelledby="importAdminSkillsTabPaste" hidden>
                        <div class="admin-skill-import-paste-header">
                            <label for="importAdminSkillsPasteInput" data-i18n="admin_skills_import_paste_label">Paste your SKILL.md content</label>
                            <button type="button" class="admin-skill-import-paste-clear" id="importAdminSkillsPasteClear" hidden data-i18n="admin_skills_import_paste_clear">Clear</button>
                        </div>
                        <textarea id="importAdminSkillsPasteInput" class="admin-skill-import-paste" spellcheck="false" autocomplete="off" data-i18n-attr="placeholder:admin_skills_import_paste_placeholder" placeholder="---&#10;name: my-skill&#10;description: What this skill does and when to use it.&#10;---&#10;&#10;# Instructions"></textarea>
                    </div>
                </div>
                <div class="provider-import-controls" id="importAdminSkillsSelectionControls" hidden><label class="provider-import-select-all"><input type="checkbox" id="importAdminSkillsSelectAll"><span data-i18n="modal_select_all">Select all</span></label><div class="provider-import-file" id="importAdminSkillsFileName"></div></div>
                <div class="provider-import-list" id="importAdminSkillsList" role="listbox" aria-multiselectable="true"></div>
                <div class="provider-import-status" id="importAdminSkillsStatus" role="alert" hidden></div>
            `,
            actions: [cancel('importAdminSkillsCancel'), submit('importAdminSkillsConfirm', 'admin_skills_import_confirm', 'Import selected', null, { disabled: true })],
        }),
        importCard({
            id: 'userChatTransferOverlay',
            titleId: 'userChatTransferTitle',
            titleI18n: 'modal_select_user_title',
            titleText: 'Select User',
            subtitleId: 'userChatTransferSubtitle',
            subtitleI18n: 'modal_select_user_subtitle',
            subtitleText: 'Choose which user should receive or export these chats.',
            closeId: 'userChatTransferClose',
            closeI18nAttr: 'modal_close_dialog_aria',
            closeAria: 'Close dialog',
            bodyHtml: `
                <div class="provider-import-controls">
                    <div class="provider-import-file" id="userChatTransferMeta"></div>
                    <div class="provider-import-option-field" id="userChatTransferReasonRow" hidden>
                        <label class="form-label" for="userChatTransferReason" data-i18n="reason_label">Reason</label>
                        <input type="text" class="form-input" id="userChatTransferReason" minlength="3" maxlength="255" autocomplete="off" aria-describedby="userChatTransferReasonDescription" data-i18n-attr="placeholder:enter_reason_placeholder" placeholder="Enter a reason">
                        <p class="provider-import-option-desc" id="userChatTransferReasonDescription" data-i18n="users_export_reason_desc">Explain why this sensitive user archive is required. The reason is recorded in the audit log.</p>
                    </div>
                    <input type="text" class="admin-search-input provider-import-search" id="userChatTransferSearch" data-i18n-attr="placeholder:users_search_placeholder;aria-label:users_search_aria" placeholder="Search by name or email..." aria-label="Search users by name or email" autocomplete="off" spellcheck="false">
                </div>
                <div class="provider-import-list" id="userChatTransferList" role="listbox" data-i18n-attr="aria-label:users_selection_aria" aria-label="User selection"></div>
                <div class="provider-import-status" id="userChatTransferStatus" role="alert" hidden></div>
            `,
            actions: [cancel('userChatTransferCancel'), submit('userChatTransferConfirm', 'btn_continue', 'Continue', 'userChatTransferConfirmText', { disabled: true })],
        }),
        importCard({
            id: 'deleteUnchangedOverlay',
            titleId: 'deleteUnchangedTitle',
            titleI18n: 'modal_discard_changes_title',
            titleText: 'Discard changes?',
            subtitleId: 'deleteUnchangedSubtitle',
            subtitleI18n: 'modal_discard_changes_desc',
            subtitleText: 'You have unsaved changes. Are you sure you want to leave without saving?',
            closeId: 'deleteUnchangedClose',
            closeI18nAttr: 'modal_close_dialog_aria',
            closeAria: 'Close dialog',
            bodyHtml: '',
            actions: [cancel('deleteUnchangedCancel', 'modal_stay_btn', 'Stay'), submit('deleteUnchangedConfirm', 'modal_discard_btn', 'Discard changes')],
        }),
        {
            id: 'deleteFeedbackOverlay',
            icon: 'warning',
            title: title(null, 'delete_feedback_title', 'Delete Feedback?'),
            descriptions: [desc(null, 'delete_feedback_desc', 'Select how much history you want to purge. This action permanently removes all feedback entries in the selected time period and cannot be undone.')],
            bodyHtml: `
                <label class="delete-warning-card-label" for="deleteFeedbackPeriodSelect" data-i18n="delete_feedback_period_label">Time period</label>
                <select id="deleteFeedbackPeriodSelect" class="feedback-stats-select stats-select">
                    <option value="7" data-i18n="period_last_7_days">Last 7 days</option>
                    <option value="14" data-i18n="period_last_14_days">Last 14 days</option>
                    <option value="30" selected data-i18n="period_last_30_days">Last 30 days</option>
                    <option value="90" data-i18n="period_last_90_days">Last 90 days</option>
                    <option value="180" data-i18n="period_last_180_days">Last 180 days</option>
                    <option value="365" data-i18n="period_last_year">Last year</option>
                </select>
            `,
            actions: [cancel('deleteFeedbackCancelBtn'), danger('deleteFeedbackConfirmBtn', 'delete_feedback_confirm_btn', 'Delete Feedback', 'deleteFeedbackConfirmText')],
        },
        {
            id: 'deleteNotificationsModal',
            icon: 'warning',
            title: title(null, 'notif_delete_all_modal_title', 'Delete All Notifications?'),
            descriptions: [desc(null, 'notif_delete_all_modal_desc', 'This will permanently delete all admin notifications. This action cannot be undone.')],
            actions: [cancel('cancelDeleteNotifications'), danger('confirmDeleteNotifications', 'notif_delete_all_btn', 'Delete All')],
        },
        {
            id: 'dataControlsBulkImportOverlay',
            icon: 'file',
            iconClass: 'delete-warning-card-icon--info',
            cardClass: 'delete-warning-card--wide',
            title: title(null, 'data_controls_bulk_modal_title', 'Bulk Import - All Users'),
            descriptions: [desc(null, 'data_controls_bulk_modal_desc', 'Upload both files to match chats to local users by email.')],
            bodyHtml: `
                <div class="dc-bulk-file-row">
                    <div class="dc-bulk-file-label"><span class="dc-bulk-file-num">1</span><div><strong data-i18n="dc_bulk_csv_label">Users CSV</strong><small data-i18n="dc_bulk_csv_hint">Open WebUI user list with id, name, email columns</small></div></div>
                    <button type="button" class="om-button border cancel dc-bulk-file-btn" id="dcBulkCsvBtn"><span data-i18n="dc_bulk_select_csv">Select CSV</span></button>
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <input type="file" id="dcBulkCsvInput" accept=".csv,text/csv" hidden>
                        <span class="dc-bulk-file-status" id="dcBulkCsvStatus"></span>
                    </div>
                </div>
                <div class="dc-bulk-file-row">
                    <div class="dc-bulk-file-label"><span class="dc-bulk-file-num">2</span><div><strong data-i18n="dc_bulk_json_label">All Chats JSON</strong><small data-i18n="dc_bulk_json_hint">Open WebUI all-chats export file</small></div></div>
                    <button type="button" class="om-button border cancel dc-bulk-file-btn" id="dcBulkJsonBtn"><span data-i18n="dc_bulk_select_json">Select JSON</span></button>
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <input type="file" id="dcBulkJsonInput" accept=".json,application/json" hidden>
                        <span class="dc-bulk-file-status" id="dcBulkJsonStatus"></span>
                    </div>
                </div>
                <div id="dcBulkSummary" class="dc-import-summary" hidden></div>
            `,
            actions: [cancel('dcBulkCancelBtn'), submit('dcBulkConfirmBtn', 'dc_bulk_start_import', 'Start Import', null, { disabled: true })],
        },
        {
            id: 'dataControlsUserSelectOverlay',
            icon: 'user',
            iconClass: 'delete-warning-card-icon--info',
            cardClass: 'delete-warning-card--medium',
            title: title(null, 'dc_user_select_title', 'Select Target User'),
            descriptions: [desc(null, 'dc_user_select_desc', 'Choose which user the imported chats should be assigned to.')],
            bodyHtml: `
                <div class="dc-user-search-wrapper">
                    <input type="text" id="dataControlsUserSearch" class="dc-user-search-input" data-i18n-attr="placeholder:dc_user_search_placeholder" placeholder="Search users by name or email..." autocomplete="off" spellcheck="false">
                </div>
                <div class="dc-user-list" id="dataControlsUserList"></div>
                <div id="dataControlsImportSummary" class="dc-import-summary" hidden></div>
            `,
            actions: [cancel('dataControlsUserSelectCancel'), submit('dataControlsUserSelectConfirm', 'users_import_chats_btn', 'Import Chats', null, { disabled: true })],
        },
        {
            id: 'restoreUserOverlay',
            icon: 'warning',
            title: title(null, 'modal_restore_user_title', 'Restore User?'),
            descriptions: [desc('restoreUserMessage', 'modal_restore_user_desc', 'Restore this user? They will be able to log in again.')],
            actions: [cancel('restoreUserCancelButton', 'btn_back', 'Back'), submit('restoreUserPrimaryButton', 'modal_restore_user_btn', 'Restore User', 'restoreUserPrimaryText')],
        },
        {
            id: 'hardDeleteUserOverlay',
            icon: 'warning',
            title: title(null, 'modal_hard_delete_user_title', 'Permanently Delete User?'),
            descriptions: [desc('hardDeleteUserMessage', 'modal_hard_delete_user_desc', 'This action cannot be undone. All user data will be deleted forever.')],
            actions: [cancel('hardDeleteUserCancelButton', 'btn_back', 'Back'), danger('hardDeleteUserPrimaryButton', 'modal_hard_delete_user_btn', 'Delete Permanently', 'hardDeleteUserPrimaryText')],
        },
        {
            id: 'cancelDeletionOverlay',
            icon: 'warning',
            title: title(null, 'modal_cancel_deletion_title', 'Cancel Scheduled Deletion?'),
            descriptions: [desc('cancelDeletionMessage', 'modal_cancel_deletion_desc', "Cancel scheduled permanent deletion for this user? The user will remain soft-deleted but won't be automatically purged.")],
            actions: [cancel('cancelDeletionCancelButton', 'btn_back', 'Back'), submit('cancelDeletionPrimaryButton', 'modal_cancel_deletion_btn', 'Cancel Scheduled Deletion', 'cancelDeletionPrimaryText')],
        },
        {
            id: 'deleteAdminSkillOverlay',
            icon: 'warning',
            title: title(null, 'modal_delete_skill_title', 'Delete Managed Skill?'),
            descriptions: [desc('deleteAdminSkillMessage', 'modal_delete_skill_desc', 'Are you sure you want to delete this managed skill? This action cannot be undone.')],
            actions: [cancel('deleteAdminSkillCancelButton'), danger('deleteAdminSkillPrimaryButton', 'modal_delete_skill_btn', 'Delete Skill', 'deleteAdminSkillPrimaryText')],
        },
        {
            id: 'deleteCustomToolOverlay',
            icon: 'warning',
            title: title(null, 'custom_tool_delete_title', 'Delete Custom Tool?'),
            descriptions: [desc('deleteCustomToolMessage', 'custom_tool_delete_message', 'Are you sure you want to delete this custom tool? This action cannot be undone.')],
            actions: [cancel('deleteCustomToolCancelButton'), danger('deleteCustomToolPrimaryButton', 'custom_tool_delete_button', 'Delete Tool', 'deleteCustomToolPrimaryText')],
        },
        {
            id: 'deleteGroupOverlay',
            icon: 'warning',
            title: title(null, 'modal_delete_group_title', 'Delete Group?'),
            descriptions: [desc('deleteGroupMessage', 'modal_delete_group_desc', 'Are you sure you want to delete this group? Any users in this group will join the default group.')],
            actions: [cancel('deleteGroupCancelButton'), danger('deleteGroupPrimaryButton', 'modal_delete_group_btn', 'Delete Group', 'deleteGroupPrimaryText')],
        },
        {
            id: 'deleteModelOverlay',
            icon: 'warning',
            title: title(null, 'modal_delete_model_title', 'Delete Model?'),
            descriptions: [desc('deleteModelMessage', 'modal_delete_model_desc', 'Are you sure you want to delete this model?')],
            actions: [cancel('deleteModelCancelButton'), danger('deleteModelPrimaryButton', 'modal_delete_model_btn', 'Delete Model', 'deleteModelPrimaryText')],
        },
        {
            id: 'deleteRateLimitOverlay',
            icon: 'warning',
            title: title(null, 'modal_delete_rate_limit_title', 'Delete Rate Limit?'),
            descriptions: [desc('deleteRateLimitMessage', 'modal_delete_rate_limit_desc', 'Are you sure you want to delete this rate limit?')],
            actions: [cancel('deleteRateLimitCancelButton'), danger('deleteRateLimitPrimaryButton', 'modal_delete_rate_limit_btn', 'Delete Rate Limit', 'deleteRateLimitPrimaryText')],
        },
        {
            id: 'rateLimitConflictOverlay',
            icon: 'warning',
            title: title(null, 'modal_conflicting_rate_limits_title', 'Conflicting Rate Limits'),
            descriptions: [desc(null, 'modal_conflicting_rate_limits_desc', 'This rule overlaps with an existing active rate limit. Adjust the users, groups, or models before saving.')],
            bodyHtml: '<div class="rate-limit-conflict-list" id="rateLimitConflictList"></div>',
            actions: [cancel('rateLimitConflictCancelButton', 'btn_close', 'Close'), submit('rateLimitConflictBackButton', 'rate_limit_back_to_form', 'Back to Form')],
        },
        {
            id: 'deleteUserOverlay',
            icon: 'warning',
            title: title(null, 'modal_delete_user_title', 'Delete User?'),
            descriptions: [desc('deleteUserMessage', 'modal_delete_user_desc', 'Are you sure you want to delete this user?')],
            actions: [cancel('deleteUserCancelButton'), danger('deleteUserPrimaryButton', 'modal_delete_user_btn', 'Delete User', 'deleteUserPrimaryText')],
        },
        {
            id: 'deleteProviderOverlay',
            icon: 'warning',
            title: title('deleteProviderHeaderTitle', 'modal_delete_provider_title', 'Delete Provider'),
            descriptions: [desc(null, 'modal_delete_provider_desc', 'Are you sure you want to delete this provider? This will also delete all models associated with this provider.')],
            actions: [cancel('deleteProviderCancelButton'), danger('deleteProviderPrimaryButton', 'modal_delete_provider_btn', 'Delete Provider', 'deleteProviderPrimaryText')],
        },
        {
            id: 'deleteProviderGroupWarningOverlay',
            icon: 'warning',
            iconClass: 'delete-warning-card-icon-orange',
            cardClass: 'delete-warning-card-wide',
            title: title('deleteProviderGroupWarningTitle', 'modal_provider_in_groups_title', 'Provider Used in Groups'),
            descriptions: [desc('deleteProviderGroupWarningDesc', 'modal_provider_in_groups_desc', 'This provider is part of one or more provider groups.')],
            bodyHtml: '<div class="delete-provider-group-warning-list" id="deleteProviderGroupWarningList"></div>',
            actions: [cancel('deleteProviderGroupWarningCancelButton'), danger('deleteProviderGroupWarningConfirmButton', 'modal_delete_provider_btn', 'Delete Provider', 'deleteProviderGroupWarningConfirmText')],
        },
        {
            id: 'deleteProviderGroupOverlay',
            icon: 'warning',
            title: title(null, 'modal_delete_provider_group_title', 'Delete Provider Group?'),
            descriptions: [desc('deleteProviderGroupMessage', 'modal_delete_provider_group_desc', 'Are you sure you want to delete this provider group? Models using this group will need to be reassigned.')],
            actions: [cancel('deleteProviderGroupCancelButton'), danger('deleteProviderGroupConfirmButton', 'modal_delete_group_btn', 'Delete Group', 'deleteProviderGroupConfirmText')],
        },
        {
            id: 'deleteModelStatsOverlay',
            icon: 'warning',
            title: title(null, 'modal_delete_stats_title', 'Delete All Statistics?'),
            descriptions: [{ i18n: 'modal_delete_stats_desc', html: 'Are you sure you want to delete <strong>all</strong> model statistics? This action cannot be undone. All historical data including requests, tokens, costs, and error logs will be permanently removed.' }],
            actions: [cancel('deleteModelStatsCancelBtn'), danger('deleteModelStatsConfirmBtn', 'modal_delete_stats_btn', 'Delete All Statistics', 'deleteModelStatsConfirmText')],
        },
        {
            id: 'deleteUserNotificationOverlay',
            icon: 'trash',
            title: title(null, 'modal_delete_notification_title', 'Delete Notification'),
            descriptions: [desc('deleteUserNotificationMessage', 'modal_delete_notification_desc', 'Are you sure you want to delete this notification? This action cannot be undone.')],
            actions: [cancel('deleteUserNotificationCancelBtn'), danger('deleteUserNotificationConfirmBtn', 'btn_delete', 'Delete')],
        },
    ]);
})();
