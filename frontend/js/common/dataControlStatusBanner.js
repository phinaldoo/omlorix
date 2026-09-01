(() => {
    const DEFAULT_OWNER = 'default';
    let activeOwner = null;

    /**
     * Resolve the shared banner lazily so this controller remains safe when it
     * is loaded in tests or on pages that do not render the chat shell.
     */
    const getElements = () => ({
        banner: document.getElementById('dataControlStatusBanner'),
        message: document.getElementById('dataControlStatusBannerMessage'),
        progress: document.getElementById('dataControlStatusBannerProgress'),
        bar: document.getElementById('dataControlStatusBannerBar'),
        spinner: document.querySelector('#dataControlStatusBanner .dc-status-banner__spinner'),
    });

    /** Clamp determinate progress before exposing it visually or through ARIA. */
    const normalizePercent = (percent) => {
        if (percent === null || percent === undefined || percent === '') return null;
        const parsed = Number(percent);
        if (!Number.isFinite(parsed)) return null;
        return Math.min(100, Math.max(0, parsed));
    };

    /**
     * Show or update the one application-wide long-running-operation banner.
     * The most recent caller owns the banner until it hides it or another
     * caller takes over, preventing stale async cleanup from hiding newer work.
     */
    const show = (messageText, {
        owner = DEFAULT_OWNER,
        busy = true,
        indeterminate = false,
        percent = null,
    } = {}) => {
        const elements = getElements();
        if (!elements.banner) return false;

        const normalizedPercent = normalizePercent(percent);
        const isIndeterminate = Boolean(indeterminate && busy && normalizedPercent === null);
        activeOwner = owner;

        elements.banner.hidden = false;
        elements.banner.setAttribute('aria-busy', String(Boolean(busy)));
        elements.banner.classList.toggle('dc-status-banner--indeterminate', isIndeterminate);

        if (elements.message) {
            elements.message.textContent = messageText || '';
        }
        if (elements.spinner) {
            elements.spinner.hidden = !busy;
        }
        if (elements.bar) {
            elements.bar.style.width = normalizedPercent === null
                ? (busy ? '' : '100%')
                : `${normalizedPercent}%`;
        }
        if (elements.progress) {
            if (normalizedPercent === null) {
                elements.progress.removeAttribute('aria-valuenow');
            } else {
                elements.progress.setAttribute('aria-valuenow', String(Math.round(normalizedPercent)));
            }
        }
        return true;
    };

    /** Hide and reset the banner only when the requesting operation owns it. */
    const hide = (owner = DEFAULT_OWNER) => {
        if (activeOwner !== null && activeOwner !== owner) return false;

        const elements = getElements();
        activeOwner = null;
        if (!elements.banner) return false;

        elements.banner.hidden = true;
        elements.banner.removeAttribute('aria-busy');
        elements.banner.classList.remove('dc-status-banner--indeterminate');
        if (elements.message) elements.message.textContent = '';
        if (elements.spinner) elements.spinner.hidden = false;
        if (elements.bar) elements.bar.style.width = '';
        if (elements.progress) {
            elements.progress.removeAttribute('aria-valuenow');
        }
        return true;
    };

    if (typeof window !== 'undefined') {
        window.dataControlStatusBanner = Object.freeze({ show, hide });
    }
})();
