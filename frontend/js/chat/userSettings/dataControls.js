(() => {
    const USER_DATA_EXPORT_TYPE = 'user_data';
    const USER_DATA_EXPORT_VERSION = 1.0;
    const USER_DATA_EXPORT_URL = '/api/v1/users/export';
    const USER_DATA_IMPORT_URL = '/api/v1/users/import/self';
    const CHATGPT_IMPORT_URL = '/api/v1/chats/import/chatgpt';

    const t = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const tf = (key, fallback, vars = {}) => {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_match, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    };

    const downloadButton = document.getElementById('dataControlDownloadAllButton');
    const uploadButton = document.getElementById('dataControlUploadAllButton');
    const uploadInput = document.getElementById('dataControlUploadInput');
    const archiveSection = document.getElementById('dataControlBundleSection');
    const archiveActions = document.getElementById('dataControlCta');
    const importPreview = document.getElementById('dataControlImportPreview');
    const importPreviewSummary = document.getElementById('dataControlImportPreviewSummary');
    const importPreviewWarning = document.getElementById('dataControlImportPreviewWarning');
    const importPreviewStartButton = document.getElementById('dataControlImportPreviewStart');
    const importPreviewCancelButton = document.getElementById('dataControlImportPreviewCancel');
    const chatgptSection = document.getElementById('dataControlChatGPTSection');
    const chatgptImportButton = document.getElementById('chatgptImportButton');
    const chatgptImportInput = document.getElementById('chatgptImportInput');
    const chatgptImportPreview = document.getElementById('chatgptImportPreview');
    const chatgptImportPreviewSummary = document.getElementById('chatgptImportPreviewSummary');
    const chatgptImportPreviewWarning = document.getElementById('chatgptImportPreviewWarning');
    const chatgptImportStartButton = document.getElementById('chatgptImportStart');
    const chatgptImportCancelButton = document.getElementById('chatgptImportCancel');
    const statusBanner = window.dataControlStatusBanner;
    const statusBannerOwner = 'data-controls';

    if (!downloadButton && !uploadButton && !chatgptImportButton) {
        return;
    }

    let pendingImport = null;
    let pendingChatGPTImport = null;

    /**
     * Keep button labels stable while making asynchronous state available to
     * assistive technology. Detailed progress is reported in the shared banner.
     */
    const setLoading = (button, isLoading) => {
        if (!button) return;
        button.disabled = isLoading;
        if (isLoading) {
            button.setAttribute('aria-busy', 'true');
        } else {
            button.removeAttribute('aria-busy');
        }
    };

    /** Display one concise, translated operation status above the archive UI. */
    const showStatus = (message, { busy = true } = {}) => {
        statusBanner?.show(message, {
            owner: statusBannerOwner,
            busy,
            indeterminate: busy,
            percent: busy ? null : 100,
        });
    };

    /** Reset the shared status banner after an operation has finished. */
    const hideStatus = () => {
        statusBanner?.hide(statusBannerOwner);
    };

    /**
     * Convert API failures into the same user-facing error behavior used by
     * other settings pages without leaking non-JSON server responses.
     */
    const throwFetchError = async (response) => {
        let detail = t('us_data_control_error_unexpected_response', 'Unexpected server response.');
        if (response?.status === 413) {
            detail = t(
                'us_data_control_error_archive_too_large',
                'The selected archive is larger than the current upload limit. Please ask an administrator to increase the server upload size.',
            );
        } else {
            try {
                const payload = await response.json();
                if (typeof payload?.detail === 'string' && payload.detail.trim()) {
                    detail = payload.detail.trim();
                }
            } catch (_error) {
                // The stable translated fallback is safer than rendering HTML
                // or a reverse-proxy error document in the settings page.
            }
        }
        throw new Error(detail);
    };

    /** Keep third-party archive errors translated instead of rendering parser internals. */
    const throwChatGPTFetchError = (response) => {
        let detail = t(
            'us_data_control_chatgpt_error_import_failed',
            'Failed to import the ChatGPT archive.',
        );
        if (response?.status === 400 || response?.status === 422) {
            detail = t(
                'us_data_control_chatgpt_error_invalid_archive',
                'Select a non-empty ChatGPT export ZIP archive.',
            );
        } else if (response?.status === 403) {
            detail = t(
                'us_data_control_chatgpt_error_disabled',
                'ChatGPT archive import is disabled for your group.',
            );
        } else if (response?.status === 413) {
            detail = t(
                'us_data_control_error_archive_too_large',
                'The selected archive is larger than the current upload limit. Please ask an administrator to increase the server upload size.',
            );
        }
        throw new Error(detail);
    };

    /** Save the streamed JSON response without materializing it in JavaScript. */
    const downloadResponse = async (response) => {
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        const timestamp = new Date().toISOString().replace(/:/g, '-');
        link.href = url;
        link.download = `user-data-export-${timestamp}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 500);
    };

    /**
     * Download the only supported self-service portability artifact.
     *
     * The backend owns archive composition, so every section is covered by one
     * authorization check and one audited request.
     */
    const downloadCompleteArchive = async () => {
        setLoading(downloadButton, true);
        showStatus(t('us_data_control_status_working', 'Working...'));
        try {
            const response = await window.authedFetch(USER_DATA_EXPORT_URL, { method: 'GET' });
            if (!response.ok) await throwFetchError(response);
            await downloadResponse(response);
            showStatus(t('us_data_control_success_bundle_downloaded', 'Complete account archive downloaded.'), { busy: false });
            window.notifySuccess?.(
                t('us_data_control_success_bundle_downloaded', 'Complete account archive downloaded.'),
            );
        } catch (error) {
            console.error('[dataControls] complete archive export failed', error);
            window.notifyError?.(
                error.message || t('us_data_control_error_bundle_export_failed', 'Failed to export account archive.'),
            );
            hideStatus();
        } finally {
            setLoading(downloadButton, false);
        }
    };

    /**
     * Validate the outer archive contract before showing the inline confirmation.
     * Feature-owned validators still run on the server for every nested section.
     */
    const prepareCompleteArchiveImport = async (file) => {
        closeChatGPTImportPreview({ restoreFocus: false });
        const rawText = await file.text();
        let payload;
        try {
            payload = JSON.parse(rawText);
        } catch (_error) {
            throw new Error(t('us_data_control_error_unsupported_format', 'Unsupported file format. Please select a complete JSON account archive.'));
        }
        if (
            !payload
            || payload.export_type !== USER_DATA_EXPORT_TYPE
            || Number(payload.export_version) !== USER_DATA_EXPORT_VERSION
        ) {
            throw new Error(t('us_data_control_error_unsupported_format', 'Unsupported file format. Please select a complete JSON account archive.'));
        }

        pendingImport = { file, payload };
        const sectionCount = Object.keys(payload).filter(
            (key) => !['export_type', 'export_version'].includes(key),
        ).length;
        if (importPreviewSummary) {
            importPreviewSummary.textContent = tf(
                'us_data_control_import_preview_summary',
                'Ready to merge {count} sections from {fileName}.',
                { count: sectionCount, fileName: file.name },
            );
        }
        if (importPreviewWarning) {
            importPreviewWarning.textContent = t(
                'us_data_control_import_preview_merge_warning',
                'Import adds data to this account. It does not replace the account or restore server settings.',
            );
        }
        if (importPreview) importPreview.hidden = false;
        importPreviewStartButton?.focus();
    };

    /** Clear parsed archive data whenever the user cancels or completes import. */
    const closeImportPreview = ({ restoreFocus = true } = {}) => {
        pendingImport = null;
        if (importPreview) importPreview.hidden = true;
        if (importPreviewSummary) importPreviewSummary.textContent = '';
        if (importPreviewWarning) importPreviewWarning.textContent = '';
        if (restoreFocus) uploadButton?.focus();
    };

    /**
     * Submit one complete archive to the single self-service mutation boundary.
     * The returned section list is broadcast so already-open workspace views can
     * refresh after a successful merge.
     */
    const importCompleteArchive = async () => {
        if (!pendingImport) return;

        setLoading(uploadButton, true);
        setLoading(importPreviewStartButton, true);
        if (importPreviewCancelButton) importPreviewCancelButton.disabled = true;
        showStatus(t('us_data_control_status_importing', 'Importing...'));

        try {
            const response = await window.authedFetch(USER_DATA_IMPORT_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(pendingImport.payload),
            });
            if (!response.ok) await throwFetchError(response);

            const result = await response.json();
            const importedSections = Array.isArray(result?.imported) ? result.imported : [];
            const warnings = Array.isArray(result?.warnings) ? result.warnings : [];
            const errors = Array.isArray(result?.errors) ? result.errors : [];
            window.dispatchEvent(new CustomEvent('dataControls:importedDataChanged', {
                detail: { sections: importedSections },
            }));

            if (errors.length || warnings.length) {
                const resultMessage = tf(
                    'us_data_control_import_finished_with_problems',
                    'Import finished. Imported: {completed}, failed: {failed}, needs review: {uncertain}.',
                    {
                        completed: importedSections.length,
                        failed: errors.length,
                        uncertain: warnings.length,
                    },
                );
                if (errors.length) {
                    window.notifyError?.(resultMessage);
                } else {
                    window.notifyWarning?.(resultMessage);
                }
                showStatus(resultMessage, { busy: false });
            } else {
                const resultMessage = tf(
                    'us_data_control_success_bundle_imported',
                    'Complete account archive imported.',
                    { successes: importedSections.length, failureSuffix: '' },
                );
                window.notifySuccess?.(resultMessage);
                showStatus(resultMessage, { busy: false });
            }
            closeImportPreview();
        } catch (error) {
            console.error('[dataControls] complete archive import failed', error);
            window.notifyError?.(
                error.message || t('us_data_control_error_bundle_import_failed', 'Failed to import account archive.'),
            );
            hideStatus();
        } finally {
            setLoading(uploadButton, false);
            setLoading(importPreviewStartButton, false);
            if (importPreviewCancelButton) importPreviewCancelButton.disabled = false;
        }
    };

    /** Parse the selected archive without mutating account data. */
    const handleArchiveSelection = async () => {
        const file = uploadInput?.files?.[0];
        if (!file) return;

        setLoading(uploadButton, true);
        showStatus(t('us_data_control_status_preflighting_bundle', 'Checking the complete archive before making changes...'));
        try {
            await prepareCompleteArchiveImport(file);
            hideStatus();
        } catch (error) {
            console.error('[dataControls] complete archive preflight failed', error);
            closeImportPreview();
            window.notifyError?.(
                error.message || t('us_data_control_error_bundle_import_failed', 'Failed to import account archive.'),
            );
            hideStatus();
        } finally {
            setLoading(uploadButton, false);
            uploadInput.value = '';
        }
    };

    /** Show an inline confirmation without reading the potentially large ZIP in memory. */
    const prepareChatGPTArchiveImport = (file) => {
        closeImportPreview({ restoreFocus: false });
        const fileName = String(file?.name || '').trim();
        if (!fileName.toLowerCase().endsWith('.zip') || (Number.isFinite(file?.size) && file.size <= 0)) {
            throw new Error(t(
                'us_data_control_chatgpt_error_invalid_archive',
                'Select a non-empty ChatGPT export ZIP archive.',
            ));
        }

        pendingChatGPTImport = file;
        if (chatgptImportPreviewSummary) {
            chatgptImportPreviewSummary.textContent = tf(
                'us_data_control_chatgpt_preview_summary',
                'Ready to import conversations from {fileName}.',
                { fileName },
            );
        }
        if (chatgptImportPreviewWarning) {
            chatgptImportPreviewWarning.textContent = t(
                'us_data_control_chatgpt_preview_warning',
                'Chats and supported attachments will be added to this account. Previously imported ChatGPT conversations will be skipped.',
            );
        }
        if (chatgptImportPreview) chatgptImportPreview.hidden = false;
        chatgptImportStartButton?.focus();
    };

    /** Clear the selected third-party archive without retaining its File handle. */
    const closeChatGPTImportPreview = ({ restoreFocus = true } = {}) => {
        pendingChatGPTImport = null;
        if (chatgptImportPreview) chatgptImportPreview.hidden = true;
        if (chatgptImportPreviewSummary) chatgptImportPreviewSummary.textContent = '';
        if (chatgptImportPreviewWarning) chatgptImportPreviewWarning.textContent = '';
        if (restoreFocus) chatgptImportButton?.focus();
    };

    /** Select a ChatGPT ZIP and leave all archive parsing to the bounded backend parser. */
    const handleChatGPTArchiveSelection = () => {
        const file = chatgptImportInput?.files?.[0];
        if (!file) return;

        try {
            prepareChatGPTArchiveImport(file);
        } catch (error) {
            console.error('[dataControls] ChatGPT archive selection failed', error);
            closeChatGPTImportPreview();
            window.notifyError?.(
                error.message || t('us_data_control_chatgpt_error_import_failed', 'Failed to import the ChatGPT archive.'),
            );
        } finally {
            chatgptImportInput.value = '';
        }
    };

    /** Upload the original ZIP as multipart data and refresh affected workspaces. */
    const importChatGPTArchive = async () => {
        if (!pendingChatGPTImport) return;

        setLoading(chatgptImportButton, true);
        setLoading(chatgptImportStartButton, true);
        if (chatgptImportCancelButton) chatgptImportCancelButton.disabled = true;
        showStatus(t('us_data_control_chatgpt_status_importing', 'Importing ChatGPT conversations...'));

        try {
            const formData = new FormData();
            formData.append('archive', pendingChatGPTImport);
            const response = await window.authedFetch(CHATGPT_IMPORT_URL, {
                method: 'POST',
                body: formData,
            });
            if (!response.ok) throwChatGPTFetchError(response);

            const result = await response.json();
            const importedChats = Number(result?.imported_chats) || 0;
            const importedMessages = Number(result?.imported_messages) || 0;
            const importedFiles = Number(result?.imported_files) || 0;
            const skippedChats = Number(result?.skipped_chats) || 0;
            const skippedDuplicates = Number(result?.skipped_duplicates) || 0;
            const resultMessage = tf(
                'us_data_control_chatgpt_result',
                'ChatGPT import finished. Imported {chats} chats, {messages} messages, and {files} files. Skipped {skipped} chats ({duplicates} duplicates).',
                {
                    chats: importedChats,
                    messages: importedMessages,
                    files: importedFiles,
                    skipped: skippedChats,
                    duplicates: skippedDuplicates,
                },
            );

            window.dispatchEvent(new CustomEvent('dataControls:importedDataChanged', {
                detail: {
                    sections: importedFiles > 0 ? ['chats', 'files'] : ['chats'],
                    refreshChats: importedChats > 0,
                    refreshFiles: importedFiles > 0,
                },
            }));
            if (skippedChats > skippedDuplicates) window.notifyError?.(resultMessage);
            else window.notifySuccess?.(resultMessage);
            showStatus(resultMessage, { busy: false });
            closeChatGPTImportPreview();
        } catch (error) {
            console.error('[dataControls] ChatGPT archive import failed', error);
            window.notifyError?.(
                error.message || t('us_data_control_chatgpt_error_import_failed', 'Failed to import the ChatGPT archive.'),
            );
            hideStatus();
        } finally {
            setLoading(chatgptImportButton, false);
            setLoading(chatgptImportStartButton, false);
            if (chatgptImportCancelButton) chatgptImportCancelButton.disabled = false;
        }
    };

    /**
     * Expose only the unified account-archive policy to user-settings startup.
     * Category feature switches no longer participate in portability decisions.
     */
    const updateDataControlAvailability = (flags = {}) => {
        const isAllowed = Boolean(flags.allow_user_data);
        if (archiveSection) archiveSection.style.display = isAllowed ? '' : 'none';
        if (archiveActions) archiveActions.style.display = isAllowed ? '' : 'none';
        if (chatgptSection) chatgptSection.style.display = isAllowed ? '' : 'none';
        return { anyEnabled: isAllowed, allEnabled: isAllowed };
    };

    downloadButton?.addEventListener('click', downloadCompleteArchive);
    uploadButton?.addEventListener('click', () => uploadInput?.click());
    uploadInput?.addEventListener('change', handleArchiveSelection);
    importPreviewStartButton?.addEventListener('click', importCompleteArchive);
    importPreviewCancelButton?.addEventListener('click', closeImportPreview);
    chatgptImportButton?.addEventListener('click', () => chatgptImportInput?.click());
    chatgptImportInput?.addEventListener('change', handleChatGPTArchiveSelection);
    chatgptImportStartButton?.addEventListener('click', importChatGPTArchive);
    chatgptImportCancelButton?.addEventListener('click', closeChatGPTImportPreview);

    if (typeof window !== 'undefined') {
        window.updateDataControlAvailability = updateDataControlAvailability;
    }
})();
