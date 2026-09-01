(function () {
    const MANAGED_INERT_ATTR = 'data-privacy-policy-notice-managed-inert';
    const FOCUSABLE_SELECTOR = [
        'a[href]',
        'button:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
    ].join(', ');

    let modalOverlayEl = null;
    let modalDialogEl = null;
    let modalErrorEl = null;
    let returnFocusEl = null;
    let managedInertElements = [];
    let modalKeydownHandler = null;
    let modalFocusinHandler = null;
    let escapeRegistration = null;
    let isSubmitting = false;

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const getPolicy = () => {
        const policy = window.chatSetup?.privacy_policy_notice || {};
        return {
            revision: Number(policy.revision || 0),
            notice_mode: String(policy.notice_mode || 'none'),
            notice_message_html: typeof policy.notice_message_html === 'string' ? policy.notice_message_html : '',
            should_show_notice: Boolean(policy.should_show_notice),
        };
    };

    const getDefaultBodyHtml = () => {
        const text = t(
            'privacy_policy_notice_default_message',
            'We updated our privacy policy. Please review the latest version.'
        );
        return `<p>${text}</p>`;
    };

    const getNoticeBodyHtml = (policy) => {
        const custom = String(policy.notice_message_html || '').trim();
        const rawHtml = custom || getDefaultBodyHtml();
        if (window.ChatSanitizer && typeof window.ChatSanitizer.sanitizePolicyNoticeHtml === 'function') {
            return window.ChatSanitizer.sanitizePolicyNoticeHtml(rawHtml);
        }
        return rawHtml;
    };

    const updateLocalPolicyState = ({ dismissed = false } = {}) => {
        const policy = window.chatSetup?.privacy_policy_notice;
        if (!policy) return;

        policy.should_show_notice = false;
        policy.privacy_policy_last_interacted_revision = Number(policy.revision || 0) || null;
        window.dispatchEvent(
            new CustomEvent('privacyPolicyNoticeResolved', {
                detail: {
                    dismissed: Boolean(dismissed),
                },
            })
        );
    };

    const getModalFocusableElements = () => {
        if (!modalDialogEl || typeof modalDialogEl.querySelectorAll !== 'function') return [];

        return Array.from(modalDialogEl.querySelectorAll(FOCUSABLE_SELECTOR)).filter((element) => {
            if (element.hidden || element.disabled || element.getAttribute?.('aria-hidden') === 'true') {
                return false;
            }
            if (element.getAttribute?.('tabindex') === '-1') return false;
            if (element.closest?.('[hidden], [inert], [aria-hidden="true"]')) return false;
            return typeof element.getClientRects !== 'function' || element.getClientRects().length > 0;
        });
    };

    const focusModal = (preferredTarget = null) => {
        if (!modalOverlayEl || !modalDialogEl) return;
        const target = preferredTarget || getModalFocusableElements()[0] || modalDialogEl;
        target.focus?.({ preventScroll: true });
    };

    const makeBackgroundInert = (overlay) => {
        managedInertElements = Array.from(document.body?.children || []).filter((element) => (
            element !== overlay && !element.hasAttribute?.('inert')
        ));
        managedInertElements.forEach((element) => {
            element.setAttribute('inert', '');
            element.setAttribute(MANAGED_INERT_ATTR, '');
        });
    };

    const restoreBackgroundInteraction = () => {
        managedInertElements.forEach((element) => {
            if (!element.hasAttribute?.(MANAGED_INERT_ATTR)) return;
            element.removeAttribute('inert');
            element.removeAttribute(MANAGED_INERT_ATTR);
        });
        managedInertElements = [];
    };

    const removeModalEventHandlers = () => {
        if (modalKeydownHandler) {
            document.removeEventListener?.('keydown', modalKeydownHandler, true);
            modalKeydownHandler = null;
        }
        if (modalFocusinHandler) {
            document.removeEventListener?.('focusin', modalFocusinHandler, true);
            modalFocusinHandler = null;
        }
        if (escapeRegistration) {
            if (typeof escapeRegistration.unregister === 'function') {
                escapeRegistration.unregister();
            } else if (typeof window.unregisterEscapeHandler === 'function') {
                window.unregisterEscapeHandler(escapeRegistration.id);
            }
            escapeRegistration = null;
        }
    };

    const closeModal = () => {
        const overlay = modalOverlayEl;
        if (!overlay) return;

        removeModalEventHandlers();
        modalOverlayEl = null;
        modalDialogEl = null;
        modalErrorEl = null;
        overlay.remove();
        restoreBackgroundInteraction();

        const focusTarget = returnFocusEl;
        returnFocusEl = null;
        if (focusTarget?.focus && focusTarget.isConnected !== false) {
            focusTarget.focus({ preventScroll: true });
        }
    };

    const setModalError = (message = '') => {
        if (!modalErrorEl) return false;
        modalErrorEl.textContent = String(message || '');
        modalErrorEl.hidden = !modalErrorEl.textContent;
        return true;
    };

    const postNoticeAction = async (action, revision) => {
        if (isSubmitting) return false;
        isSubmitting = true;
        setModalError();
        try {
            const response = await window.authedFetch('/api/v1/users/privacy-policy/notice', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ action, revision }),
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload?.detail || `Failed (${response.status})`);
            }
            return true;
        } catch (error) {
            const message = t('privacy_policy_notice_action_failed', 'Failed to update privacy policy notice status.');
            if (setModalError(message)) {
                console.error(message, error);
            } else if (typeof window?.notifyError === 'function') {
                window.notifyError(message);
            } else {
                console.error(message, error);
            }
            return false;
        } finally {
            isSubmitting = false;
        }
    };

    const openPrivacyPage = () => {
        window.open('/privacy', '_blank', 'noopener');
    };

    const renderModal = (policy) => {
        if (modalOverlayEl) return;

        returnFocusEl = document.activeElement || null;
        const overlay = document.createElement('div');
        overlay.className = 'warning-overlay shared-modal-overlay active';
        overlay.innerHTML = `
            <div class="warning-card shared-modal shared-modal--compact shared-modal--fit" role="dialog" aria-modal="true" aria-labelledby="privacyPolicyNoticeModalTitle" aria-describedby="privacyPolicyNoticeModalBody" tabindex="-1">
                <header class="shared-modal-header shared-modal-header--main">
                    <h3 class="privacy-policy-notice-modal-title shared-modal-title" id="privacyPolicyNoticeModalTitle">${t('privacy_policy_notice_modal_title', 'Privacy policy updated')}</h3>
                </header>
                <div class="shared-modal-body">
                    <div class="privacy-policy-notice-modal-body" id="privacyPolicyNoticeModalBody">${getNoticeBodyHtml(policy)}</div>
                    <p class="privacy-policy-notice-modal-error" data-action-error role="alert" hidden></p>
                </div>
                <footer class="privacy-policy-notice-modal-actions shared-modal-footer">
                    <button type="button" class="om-button border cancel" data-action="view">${t('privacy_policy_notice_view_policy', 'View policy')}</button>
                    <button type="button" class="om-button border submit" data-action="dismiss">${t('privacy_policy_notice_dismiss', 'Dismiss')}</button>
                </footer>
            </div>
        `;

        const dialog = overlay.querySelector('.warning-card');
        const viewButton = overlay.querySelector('[data-action="view"]');
        modalErrorEl = overlay.querySelector('[data-action-error]');
        viewButton?.addEventListener('click', openPrivacyPage);

        const dismissNotice = async () => {
            const ok = await postNoticeAction('dismiss', policy.revision);
            if (!ok) return;
            // Remove this aria-modal before notifying deferred first-run UI.
            // Event listeners may synchronously open the welcome modal.
            closeModal();
            updateLocalPolicyState({ dismissed: true });
        };

        overlay.querySelector('[data-action="dismiss"]')?.addEventListener('click', dismissNotice);
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) {
                void dismissNotice();
            }
        });

        document.body.appendChild(overlay);
        modalOverlayEl = overlay;
        modalDialogEl = dialog;
        makeBackgroundInert(overlay);

        modalKeydownHandler = (event) => {
            if (!modalOverlayEl) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation?.();
                void dismissNotice();
                return;
            }
            if (event.key !== 'Tab') return;

            const focusable = getModalFocusableElements();
            if (!focusable.length) {
                event.preventDefault();
                focusModal();
                return;
            }

            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            const activeElement = document.activeElement;
            const focusIsOutside = !modalDialogEl?.contains?.(activeElement) || activeElement === modalDialogEl;
            if (focusIsOutside || (event.shiftKey && activeElement === first)) {
                event.preventDefault();
                (event.shiftKey ? last : first).focus?.();
            } else if (!event.shiftKey && activeElement === last) {
                event.preventDefault();
                first.focus?.();
            }
        };
        document.addEventListener('keydown', modalKeydownHandler, true);

        modalFocusinHandler = (event) => {
            if (modalOverlayEl && !modalDialogEl?.contains?.(event.target)) {
                focusModal(viewButton);
            }
        };
        document.addEventListener('focusin', modalFocusinHandler, true);

        if (typeof window.registerEscapeHandler === 'function') {
            escapeRegistration = window.registerEscapeHandler({
                id: 'privacy-policy-notice-modal',
                priority: 190,
                isActive: () => Boolean(modalOverlayEl),
                close: () => dismissNotice(),
            });
        }

        const scheduleFocus = typeof window.requestAnimationFrame === 'function'
            ? window.requestAnimationFrame.bind(window)
            : typeof window.setTimeout === 'function'
                ? (callback) => window.setTimeout(callback, 0)
                : (callback) => callback();
        scheduleFocus(() => {
            if (modalOverlayEl === overlay) focusModal(viewButton);
        });
    };

    const showPrivacyPolicyNotice = () => {
        const policy = getPolicy();
        if (!policy.should_show_notice || policy.notice_mode === 'none' || !policy.revision) {
            return;
        }

        renderModal(policy);
    };

    document.addEventListener('chatSetupReady', showPrivacyPolicyNotice);
})();
