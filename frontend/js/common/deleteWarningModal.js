(function () {
    function attr(name, value) {
        if (value === undefined || value === null || value === false) return '';
        if (value === true) return ` ${name}`;
        return ` ${name}="${String(value).replaceAll('"', '&quot;')}"`;
    }

    function attrs(values = {}) {
        return Object.entries(values).map(([name, value]) => attr(name, value)).join('');
    }

    function escapeHtml(value) {
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function icon(name = 'warning') {
        if (name === 'trash') return window.Icons?.trash || '';
        if (name === 'file') return window.Icons?.file || '';
        if (name === 'user') return window.Icons?.users || window.Icons?.user || '';
        if (name === 'info') return window.Icons?.info || '';
        return window.Icons?.warning || '';
    }

    function textNode(tag, className, spec = {}) {
        if (!spec) return '';
        const nodeAttrs = {
            id: spec.id,
            class: className,
            'data-i18n': spec.i18n,
            ...spec.attrs,
        };
        const content = spec.html !== undefined ? spec.html : escapeHtml(spec.text ?? '');
        return `<${tag}${attrs(nodeAttrs)}>${content}</${tag}>`;
    }

    function actionButton(action = {}) {
        const variant = action.variant || (action.role === 'cancel' ? 'cancel' : 'danger');
        const className = ['om-button border', variant, action.className].filter(Boolean).join(' ');
        const content = action.html !== undefined
            ? action.html
            : `${action.icon ? icon(action.icon) : ''}<span${attrs({ id: action.textId, 'data-i18n': action.i18n })}>${escapeHtml(action.text ?? '')}</span>`;
        return `<button${attrs({
            type: action.type || 'button',
            class: className,
            id: action.id,
            disabled: action.disabled,
            style: action.style,
            ...action.attrs,
        })}>${content}</button>`;
    }

    function actions(items = [], leadHtml = '') {
        if (!items.length && !leadHtml) return '';
        return `<footer class="warning-navigation shared-modal-footer">${leadHtml}${items.map(actionButton).join('')}</footer>`;
    }

    /**
     * Connect an overlay's empty backdrop to the modal's existing dismissal
     * control. Reusing that control is important because feature modules often
     * perform extra cleanup (resetting fields, restoring focus, and so on) in
     * their normal cancel handlers.
     *
     * Only clicks whose target is the overlay itself are handled. Clicks on the
     * dialog card or any of its descendants must never dismiss the modal.
     */
    function bindBackdropDismissal(overlay, dismissalControl) {
        if (!overlay || !dismissalControl) return;

        overlay.addEventListener('click', (event) => {
            if (event.target !== overlay) return;

            // Disabled cancel controls usually indicate an in-flight operation.
            // In that state the backdrop must not provide a way around the lock.
            if (dismissalControl.disabled) return;

            if (typeof dismissalControl === 'function') {
                dismissalControl();
                return;
            }

            dismissalControl.click?.();
        });
    }

    function create(config = {}) {
        const overlay = document.createElement('div');
        overlay.id = config.id;
        overlay.className = ['delete-warning-overlay', 'shared-modal-overlay', config.overlayClass].filter(Boolean).join(' ');
        if (config.hidden !== false) overlay.hidden = true;
        overlay.setAttribute('aria-hidden', config.hidden === false ? 'false' : 'true');
        Object.entries(config.overlayAttrs || {}).forEach(([name, value]) => {
            overlay.setAttribute(name, String(value));
        });

        const cardClass = ['delete-warning-card', 'shared-modal', 'shared-modal--fit', config.cardClass].filter(Boolean).join(' ');
        const iconClass = ['delete-warning-card-icon', config.iconClass].filter(Boolean).join(' ');
        const generatedTitleId = config.title?.id || `${config.id}Title`;
        const labelledby = config.ariaLabelledby || (config.contentHtml === undefined ? generatedTitleId : undefined);
        const cardAttrs = attrs({
            id: config.cardId,
            class: cardClass,
            role: config.role ?? 'dialog',
            'aria-modal': config.ariaModal ?? 'true',
            'aria-labelledby': labelledby,
            'aria-describedby': config.ariaDescribedby,
            tabindex: config.tabindex ?? '-1',
            style: config.cardStyle,
            ...config.cardAttrs,
        });
        // A modal may place explanatory copy before its action buttons while
        // still using the shared footer and button renderer. The caller owns
        // and translates this trusted HTML, just like `contentHtml` above.
        const footer = actions(config.actions, config.actionsLeadHtml || '');
        const descriptions = (config.descriptions || []).map((description) => textNode('p', 'delete-warning-card-desc', description)).join('');
        const body = config.bodyHtml || '';
        const content = config.contentHtml !== undefined
            ? `${config.contentHtml}${footer}`
            : `
                <header class="shared-modal-header shared-modal-header--main">
                    ${textNode('h3', 'delete-warning-card-title shared-modal-title', { ...config.title, id: generatedTitleId })}
                </header>
                <div class="shared-modal-body shared-modal-body--centered">
                    <div class="${iconClass}"${attrs(config.iconAttrs)}>${config.iconHtml || icon(config.icon)}</div>
                    ${descriptions}
                    ${body}
                </div>
                ${footer}
            `;

        overlay.innerHTML = `
            <div${cardAttrs}>
                ${content}
            </div>
        `;

        // Custom modal bodies own their headings, but the shared shell still
        // guarantees every dialog has an accessible name. Stable IDs derived
        // from the stable overlay ID avoid generated or locale-dependent keys.
        const dialog = overlay.querySelector?.('[role="dialog"]');
        if (dialog && !dialog.getAttribute('aria-labelledby') && !dialog.getAttribute('aria-label')) {
            const heading = dialog.querySelector('h1, h2, h3, [data-modal-title]');
            if (heading) {
                if (!heading.id) heading.id = generatedTitleId;
                dialog.setAttribute('aria-labelledby', heading.id);
            }
        }

        // Most feature modules already use the native hidden attribute. Keep
        // the accessibility state coupled to that single source of truth so a
        // newly opened shared dialog cannot remain hidden from assistive tech.
        if (typeof MutationObserver === 'function') {
            new MutationObserver(() => {
                overlay.setAttribute('aria-hidden', overlay.hidden ? 'true' : 'false');
            }).observe(overlay, { attributes: true, attributeFilter: ['hidden'] });
        }

        // Registries can mount after a cached locale has already been applied
        // to the document. Translate the new subtree immediately in that case;
        // a still-pending initial locale load will translate it in the normal
        // document-wide pass instead.
        window.translateI18nElements?.(overlay);

        // Backdrop dismissal is opt-in because some warning modals intentionally
        // block navigation or use their cancel-styled action for a different
        // purpose (for example, clearing a form instead of closing it).
        if (config.backdropDismissControlId) {
            const dismissalControl = Array.from(overlay.querySelectorAll('[id]'))
                .find((element) => element.id === config.backdropDismissControlId);
            bindBackdropDismissal(overlay, dismissalControl);
        }

        return overlay;
    }

    /**
     * Mounts shared warning/delete card definitions before page modules query
     * their IDs. Definitions keep modal-specific content in page registries while
     * this helper owns the repeated overlay, card, icon, and footer structure.
     */
    function mountAll(configs = [], target = document.body) {
        configs.forEach((config) => {
            if (!config?.id || document.getElementById(config.id)) return;
            target.appendChild(create(config));
        });
    }

    window.DeleteWarningModal = { bindBackdropDismissal, create, mountAll };
})();
