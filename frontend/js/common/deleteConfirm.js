(function () {
    const MODAL_ID = 'commonDeleteConfirmOverlay';
    let activeResolver = null;
    let previouslyFocused = null;
    let previousBodyOverflow = null;

    function t(key, fallback) {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function trashIcon() {
        return Icons?.trash || '';
    }

    function warningIcon() {
        return Icons.warning;
    }

    function infoIcon() {
        return Icons.info;
    }

    function iconForVariant(variant) {
        if (variant === 'info') return infoIcon();
        if (variant === 'warning') return warningIcon();
        return trashIcon();
    }

    function ensureModal() {
        let overlay = document.getElementById(MODAL_ID);
        if (overlay) return overlay;

        overlay = document.createElement('div');
        overlay.id = MODAL_ID;
        overlay.className = 'delete-warning-overlay shared-modal-overlay';
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        overlay.innerHTML = `
            <div class="delete-warning-card shared-modal shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="commonDeleteConfirmTitle" aria-describedby="commonDeleteConfirmDesc" tabindex="-1">
                <header class="shared-modal-header shared-modal-header--main">
                    <h3 class="delete-warning-card-title shared-modal-title" id="commonDeleteConfirmTitle"></h3>
                </header>
                <div class="shared-modal-body shared-modal-body--centered">
                    <div class="delete-warning-card-icon" id="commonDeleteConfirmIcon">${trashIcon()}</div>
                    <p class="delete-warning-card-desc" id="commonDeleteConfirmDesc"></p>
                    <textarea class="delete-warning-card-copy-text" id="commonDeleteConfirmCopyText" readonly hidden></textarea>
                </div>
                <footer class="warning-navigation shared-modal-footer">
                    <button type="button" class="om-button border cancel" id="commonDeleteConfirmCancel"></button>
                    <button type="button" class="om-button border danger" id="commonDeleteConfirmPrimary">
                        ${trashIcon()}
                        <span id="commonDeleteConfirmPrimaryText"></span>
                    </button>
                </footer>
            </div>
        `;

        document.body.appendChild(overlay);
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) closeModal(false);
        });
        overlay.querySelector('#commonDeleteConfirmCancel')?.addEventListener('click', () => closeModal(false));
        overlay.querySelector('#commonDeleteConfirmPrimary')?.addEventListener('click', () => closeModal(true));
        document.addEventListener('keydown', (event) => {
            if (overlay.hidden) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                closeModal(false);
                return;
            }
            if (event.key !== 'Tab') return;

            const focusable = Array.from(overlay.querySelectorAll(
                'button:not([disabled]), textarea:not([disabled]):not([hidden]), [href], [tabindex]:not([tabindex="-1"])',
            )).filter((element) => !element.hidden && element.getClientRects().length > 0);
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });

        return overlay;
    }

    function closeModal(value) {
        const overlay = document.getElementById(MODAL_ID);
        if (overlay) {
            overlay.hidden = true;
            overlay.setAttribute('aria-hidden', 'true');
        }
        if (previousBodyOverflow !== null) {
            document.body.style.overflow = previousBodyOverflow;
            previousBodyOverflow = null;
        }

        const resolver = activeResolver;
        activeResolver = null;
        if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
            previouslyFocused.focus();
        }
        previouslyFocused = null;
        if (typeof resolver === 'function') {
            resolver(Boolean(value));
        }
    }

    /**
     * Shows the shared destructive-action modal and resolves to true only when the
     * user confirms. Callers pass already-translated copy when they have
     * feature-specific text; the helper supplies translated defaults otherwise.
     */
    window.showDeleteConfirm = function showDeleteConfirm(options = {}) {
        if (activeResolver) {
            closeModal(false);
        }

        const overlay = ensureModal();
        const titleEl = overlay.querySelector('#commonDeleteConfirmTitle');
        const descEl = overlay.querySelector('#commonDeleteConfirmDesc');
        const cancelBtn = overlay.querySelector('#commonDeleteConfirmCancel');
        const primaryBtn = overlay.querySelector('#commonDeleteConfirmPrimary');
        const primaryText = overlay.querySelector('#commonDeleteConfirmPrimaryText');
        const iconEl = overlay.querySelector('#commonDeleteConfirmIcon');
        const copyTextEl = overlay.querySelector('#commonDeleteConfirmCopyText');

        const title = options.title || t('common_delete_confirm_title', 'Delete item?');
        const message = options.message || options.description || t('common_delete_confirm_desc', 'This action cannot be undone.');
        const cancelLabel = options.cancelLabel || t('common_delete_confirm_cancel', 'Cancel');
        const confirmLabel = options.confirmLabel || options.confirmText || t('common_delete_confirm_button', 'Delete');

        if (titleEl) titleEl.textContent = title;
        if (descEl) descEl.textContent = message;
        if (cancelBtn) cancelBtn.textContent = cancelLabel;
        if (primaryText) primaryText.textContent = confirmLabel;
        if (iconEl) iconEl.innerHTML = iconForVariant(options.variant || 'delete');
        if (copyTextEl) {
            const hasCopyText = options.copyText !== undefined && options.copyText !== null && String(options.copyText) !== '';
            copyTextEl.hidden = !hasCopyText;
            copyTextEl.value = hasCopyText ? String(options.copyText) : '';
        }
        if (primaryBtn) {
            const isDanger = options.danger !== false;
            primaryBtn.classList.toggle('danger', isDanger);
            primaryBtn.classList.toggle('submit', !isDanger);
            // Some confirmation flows are informative preflights. Native
            // disabled state keeps the primary action unreachable by pointer,
            // keyboard, and assistive technology while leaving Cancel/Close
            // available as the focused exit action.
            primaryBtn.disabled = Boolean(options.confirmDisabled);
            const primaryIcon = primaryBtn.querySelector('svg');
            if (primaryIcon) primaryIcon.outerHTML = iconForVariant(options.variant || 'delete');
        }

        previouslyFocused = document.activeElement;
        if (previousBodyOverflow === null) previousBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');
        if (copyTextEl && !copyTextEl.hidden) {
            copyTextEl.focus();
            copyTextEl.select();
        } else {
            cancelBtn?.focus();
        }

        return new Promise((resolve) => {
            activeResolver = resolve;
        });
    };

    window.showWarningConfirm = function showWarningConfirm(options = {}) {
        return window.showDeleteConfirm({
            ...options,
            danger: options.danger === true,
            variant: options.variant || 'warning',
            title: options.title || t('common_confirm_title', 'Continue?'),
            confirmLabel: options.confirmLabel || options.confirmText || t('common_confirm_button', 'Continue'),
        });
    };

    window.showCopyTextDialog = function showCopyTextDialog(options = {}) {
        return window.showWarningConfirm({
            ...options,
            variant: options.variant || 'info',
            title: options.title || t('common_copy_text_title', 'Copy manually'),
            message: options.message || t('common_copy_text_desc', 'Copy this value manually.'),
            cancelLabel: options.cancelLabel || t('common_close', 'Close'),
            confirmLabel: options.confirmLabel || t('common_done', 'Done'),
        });
    };
})();
