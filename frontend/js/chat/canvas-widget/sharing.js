(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function createSharingModule(deps, state) {
        const {
            escapeHtml, formatT, isShareableFileContext, notifyShareError,
            notifyShareSuccess, previewShareBtn, setActiveFileContext, shareCloseBtn,
            shareEmptySection, shareExpiryContent, shareExpiryError, shareExpiryInput,
            shareExpiryToggle, shareFileName, shareForm, shareFormTitle,
            shareLinksList, shareLinksSection, shareModal, shareNotice,
            shareOverlay, sharePasswordContent, sharePasswordError, sharePasswordHelper,
            sharePasswordInput, sharePasswordToggle, sharePrimaryBtn, shareSecondaryBtn,
            t,
        } = deps;
        function updateShareButtonState() {
            if (!previewShareBtn) return;
            const hasExistingShareLinks = Array.isArray(state.currentShareLinks) && state.currentShareLinks.length > 0;
            const canShare = Boolean(
                state.previewVisible
                && (state.sharingAllowedByGroup || hasExistingShareLinks)
                && isShareableFileContext(state.activeFileContext)
            );
            const shareTitle = canShare
                ? t('canvas_share_button_enabled', 'Share canvas')
                : (state.sharingAllowedByGroup || hasExistingShareLinks
                    ? t('canvas_share_button_unavailable', 'Share unavailable')
                    : t('canvas_share_button_disabled_admin', 'Sharing disabled by admin'));
            previewShareBtn.hidden = !canShare;
            previewShareBtn.disabled = !canShare;
            previewShareBtn.classList.toggle('is-disabled', !canShare);
            previewShareBtn.setAttribute('title', shareTitle);
            previewShareBtn.setAttribute('aria-label', shareTitle);
            if (!canShare && state.shareModalOpen) {
                closeShareModal();
            }
        }
    
        async function requestShareApi(path, init = {}) {
            const requestFn = typeof window.authedFetch === 'function' ? window.authedFetch : fetch;
            const response = await requestFn(path, init);
            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
            if (!response.ok) {
                const backendDetail = payload?.detail;
                const detail = backendDetail === 'canvas_asset_public_approval_required'
                    ? t(
                        'canvas_asset_public_approval_required',
                        'The share link will be available after the referenced file owners approve public access.',
                    )
                    : (backendDetail === 'canvas_asset_preview_stale'
                        ? t('canvas_asset_preview_stale', 'Render the current Canvas preview before creating a public link.')
                        : (backendDetail || formatT(
                    'canvas_share_request_failed_status',
                    'Request failed ({status})',
                    { status: response.status },
                        )));
                throw new Error(detail);
            }
            return payload || {};
        }
    
        function setTranslatedText(element, key, fallback) {
            if (!element) return;
            element.setAttribute('data-i18n', key);
            element.textContent = t(key, fallback);
        }
    
        function toIso(value) {
            if (!value) return null;
            const dt = new Date(value);
            return Number.isNaN(dt.getTime()) ? null : dt.toISOString();
        }
    
        function toLocalDateTimeValue(isoString) {
            if (!isoString) return '';
            const dt = new Date(isoString);
            if (Number.isNaN(dt.getTime())) return '';
            const local = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000);
            return local.toISOString().slice(0, 16);
        }
    
        function formatShareTimestamp(value) {
            const parsed = new Date(value);
            if (Number.isNaN(parsed.getTime())) return '';
            try {
                return parsed.toLocaleString(undefined, {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                });
            } catch (_) {
                return parsed.toISOString();
            }
        }
    
        function isShareExpired(value) {
            if (!value) return false;
            const parsed = new Date(value);
            return !Number.isNaN(parsed.getTime()) && parsed.getTime() < Date.now();
        }
    
        function getDefaultShareExpiryIso() {
            return new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
        }
    
        function getRequiredShareExpiryIso() {
            if (shareExpiryInput?.value) {
                return toIso(shareExpiryInput.value);
            }
            const fallback = getDefaultShareExpiryIso();
            if (shareExpiryInput) {
                shareExpiryInput.value = toLocalDateTimeValue(fallback);
            }
            return fallback;
        }
    
        function getShareLinkById(shareId) {
            const normalized = String(shareId || '').trim();
            return state.currentShareLinks.find((link) => String(link?.share_id || '') === normalized) || null;
        }
    
        function isVisibleElement(element) {
            if (!element || element.hidden) return false;
            const styles = window.getComputedStyle(element);
            return styles.display !== 'none' && styles.visibility !== 'hidden';
        }
    
        function getFocusableElements(container) {
            if (!container) return [];
            return Array.from(container.querySelectorAll(
                'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )).filter((element) => isVisibleElement(element) && !element.hasAttribute('inert'));
        }
    
        function trapFocus(event, container) {
            if (event.key !== 'Tab') return;
            const focusable = getFocusableElements(container);
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
                return;
            }
            if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    
        function setShareBusy(busy) {
            state.shareBusy = Boolean(busy);
            shareModal?.classList.toggle('cs-busy', state.shareBusy);
            [
                sharePrimaryBtn,
                shareSecondaryBtn,
                shareCloseBtn,
                sharePasswordToggle,
                sharePasswordInput,
                shareExpiryInput,
            ].forEach((element) => {
                if (element) element.disabled = state.shareBusy;
            });
            if (shareExpiryToggle) shareExpiryToggle.disabled = true;
            shareLinksList?.querySelectorAll('button').forEach((button) => {
                button.disabled = state.shareBusy;
            });
        }
    
        function showShareNotice(message, type = 'info') {
            if (!shareNotice) return;
            if (!message) {
                shareNotice.hidden = true;
                shareNotice.textContent = '';
                shareNotice.className = 'cs-notice';
                return;
            }
            shareNotice.textContent = message;
            shareNotice.className = `cs-notice cs-notice-${type}`;
            shareNotice.hidden = false;
        }
    
        function showShareControlError(inputEl, errorEl, message) {
            if (!inputEl || !errorEl) return;
            if (window.FormValidation?.showInputError) {
                window.FormValidation.showInputError(inputEl, errorEl, message, {
                    inputErrorClass: 'cs-input-error',
                    errorVisibleClass: null,
                });
                return;
            }
            errorEl.textContent = message || '';
            errorEl.hidden = false;
            inputEl.classList.add('cs-input-error');
            inputEl.setAttribute('aria-invalid', 'true');
        }
    
        function hideShareControlError(inputEl, errorEl) {
            if (!inputEl || !errorEl) return;
            if (window.FormValidation?.clearInputError) {
                window.FormValidation.clearInputError(inputEl, errorEl, {
                    inputErrorClass: 'cs-input-error',
                    errorVisibleClass: null,
                });
                return;
            }
            errorEl.hidden = true;
            errorEl.textContent = '';
            inputEl.classList.remove('cs-input-error');
            inputEl.setAttribute('aria-invalid', 'false');
        }
    
        function showSharePasswordError(message) {
            showShareControlError(sharePasswordInput, sharePasswordError, message || t('chat_share_password_min_error', 'Password must be at least 8 characters long.'));
        }
    
        function hideSharePasswordError() {
            hideShareControlError(sharePasswordInput, sharePasswordError);
        }
    
        function showShareExpiryError(message) {
            showShareControlError(shareExpiryInput, shareExpiryError, message);
        }
    
        function hideShareExpiryError() {
            hideShareControlError(shareExpiryInput, shareExpiryError);
        }
    
        async function runShareWithBusy(task) {
            if (state.shareBusy) return;
            setShareBusy(true);
            try {
                await task();
            } catch (error) {
                const message = error?.message || formatT('canvas_share_request_failed_status', 'Request failed ({status})', { status: 'unknown' });
                notifyShareError(message);
                showShareNotice(message, 'error');
            } finally {
                setShareBusy(false);
            }
        }
    
        function renderShareLinkCard(link) {
            const shareId = String(link?.share_id || '');
            const shareUrl = String(link?.share_url || '');
            const hasPassword = Boolean(link?.has_password);
            const expiresAt = link?.expires_at || null;
            const createdAt = link?.created_at || null;
            const expired = isShareExpired(expiresAt);
            const passwordChip = hasPassword
                ? `<span class="cs-chip">
                       ${Icons.lock}
                       ${escapeHtml(t('chat_share_chip_password', 'Password'))}
                   </span>`
                : '';
            const expiryChip = expiresAt
                ? `<span class="cs-chip ${expired ? 'cs-chip-danger' : ''}">
                       ${Icons.clock}
                       ${escapeHtml(expired ? t('chat_share_chip_expired', 'Expired') : t('chat_share_chip_expires', 'Expires'))} ${escapeHtml(formatShareTimestamp(expiresAt))}
                   </span>`
                : '';
            const createdChip = createdAt
                ? `<span class="cs-chip cs-chip-muted">${escapeHtml(t('chat_share_chip_created', 'Created'))} ${escapeHtml(formatShareTimestamp(createdAt))}</span>`
                : '';
    
            return `
                <div class="cs-link-card ${expired ? 'cs-link-card-expired' : ''}" data-share-id="${escapeHtml(shareId)}">
                    <div class="cs-link-url-row">
                        <input type="text" class="cs-link-url" value="${escapeHtml(shareUrl)}" readonly aria-label="${escapeHtml(t('canvas_share_link_url_aria', 'Share link URL'))}">
                    </div>
                    <div class="cs-link-meta">
                        <span class="cs-chip">
                            ${Icons.globe}
                            ${escapeHtml(t('chat_share_chip_public', 'Anyone with link'))}
                        </span>
                        ${passwordChip}
                        ${expiryChip}
                        ${createdChip}
                    </div>
                    <div class="cs-link-actions">
                        <button type="button" class="om-button border cancel" data-share-action="copy" title="${escapeHtml(t('chat_share_copy_link', 'Copy link'))}">
                            ${Icons.copy}
                            ${escapeHtml(t('chat_share_copy', 'Copy'))}
                        </button>
                        <button type="button" class="om-button border cancel" data-share-action="open">
                            ${Icons.open_window}
                            ${escapeHtml(t('chat_share_open', 'Open'))}
                        </button>
                        <button type="button" class="om-button border cancel" data-share-action="edit">
                            ${Icons.create}
                            ${escapeHtml(t('chat_share_edit', 'Edit'))}
                        </button>
                        <button type="button" class="om-button border danger-nofill" data-share-action="delete">
                            ${Icons.trash}
                            ${escapeHtml(t('chat_share_delete', 'Delete'))}
                        </button>
                    </div>
                </div>
            `;
        }
    
        function renderShareLinks(links) {
            if (!shareLinksList) return;
            const safeLinks = Array.isArray(links) ? links : [];
            state.currentShareLinks = safeLinks.slice();
            shareLinksList.innerHTML = safeLinks.map(renderShareLinkCard).join('');
        }
    
        async function loadShareLinks(fileId = state.activeFileContext?.fileId) {
            const requestedFileId = String(fileId || '').trim();
            if (!requestedFileId) return state.currentShareLinks;
            const result = await requestShareApi(`/api/v1/files/canvas/share/status?file_id=${encodeURIComponent(requestedFileId)}`, {
                method: 'GET',
                credentials: 'include',
            });
            if (state.activeFileContext?.fileId !== requestedFileId) return state.currentShareLinks;
            renderShareLinks(result.links || []);
            return state.currentShareLinks;
        }
    
        async function refreshExistingShareLinksForButton() {
            if (state.sharingAllowedByGroup || !state.activeFileContext?.fileId || !isShareableFileContext(state.activeFileContext)) return;
            const refreshToken = ++state.shareLinksRefreshToken;
            const fileId = state.activeFileContext.fileId;
            try {
                await loadShareLinks(fileId);
            } catch (_) {
                if (refreshToken === state.shareLinksRefreshToken) {
                    state.currentShareLinks = [];
                }
            }
            if (refreshToken === state.shareLinksRefreshToken) {
                updateShareButtonState();
            }
        }
    
        function applyShareMode() {
            const hasLinks = state.currentShareLinks.length > 0;
            const canCreateLinks = state.sharingAllowedByGroup;
            if (state.shareMode === 'list') {
                if (shareLinksSection) shareLinksSection.hidden = !hasLinks;
                if (shareEmptySection) shareEmptySection.hidden = hasLinks;
                if (shareForm) shareForm.hidden = true;
                renderShareLinks(state.currentShareLinks);
                if (sharePrimaryBtn) {
                    setTranslatedText(sharePrimaryBtn, hasLinks ? 'chat_share_new_link' : 'chat_share_create_link', hasLinks ? 'New link' : 'Create link');
                    sharePrimaryBtn.style.display = canCreateLinks ? '' : 'none';
                }
                if (shareSecondaryBtn) {
                    setTranslatedText(shareSecondaryBtn, 'chat_share_done', 'Done');
                    shareSecondaryBtn.style.display = '';
                }
            } else {
                if (shareLinksSection) shareLinksSection.hidden = true;
                if (shareEmptySection) shareEmptySection.hidden = true;
                if (shareForm) shareForm.hidden = false;
                if (shareFormTitle) {
                    setTranslatedText(
                        shareFormTitle,
                        state.shareMode === 'edit' ? 'chat_share_edit_link' : 'chat_share_create_new_link',
                        state.shareMode === 'edit' ? 'Edit link' : 'Create new link',
                    );
                }
                if (sharePrimaryBtn) {
                    setTranslatedText(
                        sharePrimaryBtn,
                        state.shareMode === 'edit' ? 'chat_share_save_changes' : 'chat_share_create_link',
                        state.shareMode === 'edit' ? 'Save changes' : 'Create link',
                    );
                    sharePrimaryBtn.style.display = '';
                }
                if (shareSecondaryBtn) {
                    setTranslatedText(shareSecondaryBtn, hasLinks ? 'chat_share_cancel' : 'chat_share_done', hasLinks ? 'Cancel' : 'Done');
                    shareSecondaryBtn.style.display = '';
                }
            }
            showShareNotice('');
        }
    
        function resetShareFormForCreate() {
            state.activeShareLink = null;
            if (sharePasswordToggle) sharePasswordToggle.checked = false;
            if (sharePasswordContent) sharePasswordContent.hidden = true;
            if (sharePasswordInput) {
                sharePasswordInput.value = '';
                sharePasswordInput.placeholder = t('chat_share_password_placeholder', 'Enter a password');
            }
            if (sharePasswordHelper) sharePasswordHelper.hidden = true;
            hideSharePasswordError();
            if (shareExpiryToggle) {
                shareExpiryToggle.checked = true;
                shareExpiryToggle.disabled = true;
            }
            if (shareExpiryContent) shareExpiryContent.hidden = false;
            if (shareExpiryInput) shareExpiryInput.value = toLocalDateTimeValue(getDefaultShareExpiryIso());
        }
    
        function populateShareFormFromLink(link) {
            state.activeShareLink = link || null;
            const hasPassword = Boolean(link?.has_password);
            if (sharePasswordToggle) sharePasswordToggle.checked = hasPassword;
            if (sharePasswordContent) sharePasswordContent.hidden = !hasPassword;
            if (sharePasswordInput) {
                sharePasswordInput.value = '';
                sharePasswordInput.placeholder = hasPassword
                    ? t('chat_share_password_new_placeholder', 'Enter a new password')
                    : t('chat_share_password_placeholder', 'Enter a password');
            }
            if (sharePasswordHelper) sharePasswordHelper.hidden = !hasPassword;
            hideSharePasswordError();
            if (shareExpiryToggle) {
                shareExpiryToggle.checked = true;
                shareExpiryToggle.disabled = true;
            }
            if (shareExpiryContent) shareExpiryContent.hidden = false;
            if (shareExpiryInput) {
                shareExpiryInput.value = toLocalDateTimeValue(link?.expires_at || getDefaultShareExpiryIso());
            }
        }
    
        function enterShareCreateMode() {
            state.shareMode = 'create';
            resetShareFormForCreate();
            applyShareMode();
        }
    
        function enterShareEditMode(link) {
            state.shareMode = 'edit';
            populateShareFormFromLink(link);
            applyShareMode();
        }
    
        function enterShareListMode() {
            state.shareMode = 'list';
            applyShareMode();
        }
    
        function validateShareForm() {
            hideSharePasswordError();
            hideShareExpiryError();
            showShareNotice('');
            if (sharePasswordToggle?.checked) {
                const password = String(sharePasswordInput?.value || '').trim();
                const wasSet = Boolean(state.activeShareLink?.has_password);
                if (state.shareMode === 'create' && !password) {
                    showSharePasswordError(t('chat_share_password_required_error', 'Please enter a password.'));
                    sharePasswordInput?.focus();
                    return false;
                }
                if (state.shareMode === 'edit' && !wasSet && !password) {
                    showSharePasswordError(t('chat_share_password_required_error', 'Please enter a password.'));
                    sharePasswordInput?.focus();
                    return false;
                }
                if (password && password.length < 8) {
                    showSharePasswordError(t('chat_share_password_min_error', 'Password must be at least 8 characters long.'));
                    sharePasswordInput?.focus();
                    return false;
                }
            }
            const expiryIso = getRequiredShareExpiryIso();
            if (!expiryIso) {
                const hasExpiryValue = Boolean(String(shareExpiryInput?.value || '').trim());
                showShareExpiryError(hasExpiryValue
                    ? t('chat_share_expiry_invalid_error', 'Invalid expiration date and time.')
                    : t('chat_share_expiry_required_error', 'Please pick an expiration date and time.'));
                shareExpiryInput?.focus();
                return false;
            }
            if (new Date(expiryIso).getTime() <= Date.now()) {
                showShareExpiryError(t('chat_share_expiry_future_error', 'Expiration must be in the future.'));
                shareExpiryInput?.focus();
                return false;
            }
            return true;
        }
    
        async function createShareLink() {
            if (!state.activeFileContext?.fileId || !validateShareForm()) return;
            if (!state.sharingAllowedByGroup) {
                notifyShareError(t('canvas_share_disabled_tooltip', 'Canvas sharing is disabled for your group'));
                return;
            }
            const password = String(sharePasswordInput?.value || '').trim();
            const expiresAt = getRequiredShareExpiryIso();
            try {
                await requestShareApi('/api/v1/files/canvas/share', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({
                        file_id: state.activeFileContext.fileId,
                        password: password || undefined,
                        expires_in_hours: Math.max(1, Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 3600000)),
                    }),
                });
                notifyShareSuccess(t('canvas_share_success_created', 'Share link created'));
                await loadShareLinks();
                enterShareListMode();
            } catch (error) {
                const message = error.message || t('canvas_share_error_create', 'Failed to create share link');
                notifyShareError(message);
                showShareNotice(message, 'error');
            }
        }
    
        async function copyShareUrl(url, button) {
            if (!url) return;
            try {
                await navigator.clipboard.writeText(url);
                notifyShareSuccess(t('chat_share_link_copied_notice', 'Link copied'));
            } catch (_) {
                notifyShareError(t('canvas_share_error_copy', 'Failed to copy link'));
            }
        }
    
        async function updateShareLink() {
            const shareId = String(state.activeShareLink?.share_id || '').trim();
            if (!shareId || !validateShareForm()) return;
            const previous = state.activeShareLink || {};
            const ops = [];
            const wantsPassword = Boolean(sharePasswordToggle?.checked);
            const hadPassword = Boolean(previous.has_password);
            const newPassword = String(sharePasswordInput?.value || '').trim();
            if (wantsPassword && newPassword) {
                ops.push(() => requestShareApi('/api/v1/files/canvas/share/password/change', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ share_id: shareId, password: newPassword }),
                }));
            } else if (!wantsPassword && hadPassword) {
                ops.push(() => requestShareApi('/api/v1/files/canvas/share/password/remove', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ share_id: shareId }),
                }));
            }
    
            const nextExpiry = getRequiredShareExpiryIso();
            if (nextExpiry && nextExpiry !== (previous.expires_at || null)) {
                ops.push(() => requestShareApi('/api/v1/files/canvas/share/expiry/change', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ share_id: shareId, expires_at: nextExpiry }),
                }));
            }
    
            if (!ops.length) {
                notifyShareSuccess(t('chat_share_no_changes_notice', 'No changes to save'));
                enterShareListMode();
                return;
            }
            for (const op of ops) {
                await op();
            }
            notifyShareSuccess(t('chat_share_updated_notice', 'Share link updated'));
            await loadShareLinks();
            enterShareListMode();
        }
    
        async function deleteShareLink(shareId) {
            if (!shareId) return;
            if (typeof window.showDeleteConfirm !== 'function') {
                notifyShareError(t('canvas_share_error_delete', 'Failed to remove share link'));
                return;
            }
            const confirmed = await window.showDeleteConfirm({
                message: t('canvas_share_delete_confirm', 'Delete this share link? It will stop working immediately.'),
                confirmLabel: t('chat_share_delete', 'Delete'),
            });
            if (!confirmed) return;
            try {
                await requestShareApi('/api/v1/files/canvas/share/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ share_id: shareId }),
                });
                notifyShareSuccess(t('canvas_share_success_deleted', 'Share link removed'));
                await loadShareLinks();
                enterShareListMode();
            } catch (error) {
                const message = error.message || t('canvas_share_error_delete', 'Failed to remove share link');
                notifyShareError(message);
                showShareNotice(message, 'error');
            }
        }
    
        function openShareModal() {
            if (!shareOverlay || !isShareableFileContext(state.activeFileContext)) return;
            state.shareModalReturnFocus = document.activeElement;
            if (shareFileName) {
                shareFileName.textContent = state.activeFileContext.fileName || t('canvas_share_selected_file', 'Selected file');
            }
            shareOverlay.removeAttribute('hidden');
            shareOverlay.setAttribute('aria-hidden', 'false');
            requestAnimationFrame(() => shareOverlay.classList.add('cs-active'));
            state.shareModalOpen = true;
            if (shareLinksSection) shareLinksSection.hidden = true;
            if (shareEmptySection) shareEmptySection.hidden = true;
            if (shareForm) shareForm.hidden = true;
            showShareNotice(t('canvas_share_loading', 'Loading share links...'), 'info');
            runShareWithBusy(async () => {
                await loadShareLinks();
                if (state.currentShareLinks.length) {
                    enterShareListMode();
                } else {
                    enterShareCreateMode();
                }
                setTimeout(() => {
                    const focusTarget = shareModal?.querySelector('input:not([disabled]), button:not([disabled])');
                    focusTarget?.focus();
                }, 80);
            });
        }
    
        async function openShareDialogForFile(context = {}) {
            const nextContext = {
                fileId: String(context.fileId || context.file_id || '').trim(),
                fileName: String(context.fileName || context.file_name || '').trim(),
                contentType: String(context.contentType || context.content_type || '').trim(),
            };
            if (!isShareableFileContext(nextContext)) {
                notifyShareError(t('canvas_share_unavailable_tooltip', 'This canvas cannot be shared'));
                return false;
            }
            setActiveFileContext(nextContext.fileId, nextContext.fileName, nextContext.contentType);
            if (!state.sharingAllowedByGroup) {
                await refreshExistingShareLinksForButton();
            }
            if (!state.sharingAllowedByGroup && (!Array.isArray(state.currentShareLinks) || state.currentShareLinks.length === 0)) {
                notifyShareError(t('canvas_share_disabled_tooltip', 'Canvas sharing is disabled for your group'));
                return false;
            }
            openShareModal();
            return true;
        }
    
        function closeShareModal() {
            if (!shareOverlay) return;
            shareOverlay.classList.remove('cs-active');
            shareOverlay.setAttribute('aria-hidden', 'true');
            setTimeout(() => {
                if (!state.shareModalOpen) {
                    shareOverlay.setAttribute('hidden', '');
                }
            }, 200);
            state.shareModalOpen = false;
            state.shareMode = 'list';
            state.activeShareLink = null;
            showShareNotice('');
            if (state.shareModalReturnFocus && typeof state.shareModalReturnFocus.focus === 'function') {
                state.shareModalReturnFocus.focus();
            }
            state.shareModalReturnFocus = null;
        }
    

        return Object.freeze({
            updateShareButtonState, requestShareApi, setTranslatedText, toIso,
            toLocalDateTimeValue, formatShareTimestamp, isShareExpired, getDefaultShareExpiryIso,
            getRequiredShareExpiryIso, getShareLinkById, isVisibleElement, getFocusableElements,
            trapFocus, setShareBusy, showShareNotice, showShareControlError,
            hideShareControlError, showSharePasswordError, hideSharePasswordError, showShareExpiryError,
            hideShareExpiryError, runShareWithBusy, renderShareLinkCard, renderShareLinks,
            loadShareLinks, refreshExistingShareLinksForButton, applyShareMode, resetShareFormForCreate,
            populateShareFormFromLink, enterShareCreateMode, enterShareEditMode, enterShareListMode,
            validateShareForm, createShareLink, copyShareUrl, updateShareLink,
            deleteShareLink, openShareModal, openShareDialogForFile, closeShareModal,
        });
    }

    modules.sharing = Object.freeze({ create: createSharingModule });
})(globalThis);
