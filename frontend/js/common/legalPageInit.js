(function () {
    const LEGAL_AVAILABILITY_TIMEOUT_MS = 10_000;
    const DOCUMENT_ROUTES = {
        privacy: '/privacy',
        terms: '/terms',
    };

    const DOCUMENT_CONFIGS = {
        privacy: {
            pageKey: 'privacy',
            endpoint: '/api/v1/privacy',
            pageTitle: 'Privacy Policy',
            titleKey: 'privacy_page_title',
            loadingKey: 'privacy_loading',
            loadingFallback: 'Loading privacy policy...',
            errorKey: 'privacy_load_error',
            errorFallback: 'Failed to load privacy policy. Please try again later.',
            markdown: {
                html: true,
                linkify: true,
                typographer: true,
            },
            afterRender: ({ container, payload, t }) => {
                if (!payload.customization_required) return;

                const warning = document.createElement('div');
                warning.className = 'info-box';

                const strong = document.createElement('strong');
                strong.textContent = t('privacy_default_template_warning_emphasis', 'Important:');
                warning.appendChild(strong);
                warning.appendChild(document.createTextNode(` ${t(
                    'privacy_default_template_warning',
                    'This instance is still using the default privacy policy template. The operator must replace it with deployment-specific legal content.',
                )}`));

                container.insertBefore(warning, container.firstChild);
            },
        },
        terms: {
            pageKey: 'terms',
            endpoint: '/api/v1/terms',
            pageTitle: 'Terms of Service',
            titleKey: 'terms_page_title',
            loadingKey: 'terms_loading',
            loadingFallback: 'Loading Terms of Service...',
            errorKey: 'terms_load_error',
            errorFallback: 'Failed to load Terms of Service. Please try again later.',
            markdown: {
                html: false,
                linkify: true,
                typographer: true,
            },
            afterRender: ({ container, payload, t }) => {
                if (!payload.customization_required) return;

                const warning = document.createElement('div');
                warning.className = 'info-box';

                const strong = document.createElement('strong');
                strong.textContent = t('terms_default_template_warning_emphasis', 'Important:');
                warning.appendChild(strong);
                warning.appendChild(document.createTextNode(` ${t(
                    'terms_default_template_warning',
                    'This instance is still using the default Terms of Service template. The operator must replace it with deployment-specific legal content before public signup is enabled.',
                )}`));

                container.insertBefore(warning, container.firstChild);
            },
        },
    };

    function translate(key, fallback) {
        return typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback)
            : fallback;
    }

    function getRequestedDocument() {
        const queryDocument = new URLSearchParams(window.location.search).get('document');
        if (Object.hasOwn(DOCUMENT_CONFIGS, queryDocument)) {
            return queryDocument;
        }

        const normalizedPath = window.location.pathname.replace(/\/+$/, '') || '/';
        return Object.entries(DOCUMENT_ROUTES)
            .find(([, route]) => route === normalizedPath)?.[0] || null;
    }

    function getCanonicalUrl(documentKey, includeHash = false) {
        const hash = includeHash ? window.location.hash : '';
        return `${DOCUMENT_ROUTES[documentKey]}${hash}`;
    }

    function renderLoadingState(config) {
        const container = document.getElementById('main-container');
        if (!container) return;

        const loading = document.createElement('div');
        loading.className = 'legal-loading-state';
        loading.setAttribute('role', 'status');
        loading.setAttribute('aria-live', 'polite');
        loading.textContent = translate(config.loadingKey, config.loadingFallback);
        container.replaceChildren(loading);
    }

    document.addEventListener('DOMContentLoaded', async function initializeSharedLegalPage() {
        if (!window.legalPageUtils) return;

        const documentNav = document.getElementById('legalDocumentNav');
        const documentLinks = Array.from(document.querySelectorAll('[data-legal-document]'));
        const payloadCache = new Map();
        let activeDocument = null;
        let activeCleanup = null;
        let activeAbortController = null;
        let navigationSequence = 0;

        const requestedDocument = getRequestedDocument();
        renderLoadingState(
            requestedDocument
                ? DOCUMENT_CONFIGS[requestedDocument]
                : {
                    loadingKey: 'legal_loading',
                    loadingFallback: 'Loading legal documents...',
                },
        );

        let availability = {};
        const availabilityAbortController = new AbortController();
        const availabilityTimeout = window.setTimeout(
            () => availabilityAbortController.abort(),
            LEGAL_AVAILABILITY_TIMEOUT_MS,
        );
        try {
            const response = await fetch('/api/v1/legal/availability', {
                credentials: 'same-origin',
                signal: availabilityAbortController.signal,
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            availability = await response.json();
        } catch (error) {
            console.error('Failed to load legal document availability:', error);
        } finally {
            window.clearTimeout(availabilityTimeout);
        }

        const enabledDocuments = Object.keys(DOCUMENT_CONFIGS)
            .filter((documentKey) => availability?.[documentKey] === true);

        if (documentNav) {
            documentNav.hidden = enabledDocuments.length !== 2;
        }

        documentLinks.forEach((link) => {
            const documentKey = link.dataset.legalDocument;
            link.hidden = !enabledDocuments.includes(documentKey);
        });

        function updateNavigationState(documentKey) {
            documentLinks.forEach((link) => {
                const isCurrent = link.dataset.legalDocument === documentKey;
                if (isCurrent) {
                    link.setAttribute('aria-current', 'page');
                } else {
                    link.removeAttribute('aria-current');
                }
            });
        }

        function updateDocumentTitle(documentKey) {
            const config = DOCUMENT_CONFIGS[documentKey];
            document.title = translate(config.titleKey, config.pageTitle);
        }

        async function showDocument(documentKey, options = {}) {
            const config = DOCUMENT_CONFIGS[documentKey];
            const sequence = ++navigationSequence;
            let loadedSuccessfully = false;
            activeDocument = null;

            if (typeof activeCleanup === 'function') {
                activeCleanup();
                activeCleanup = null;
            }
            activeAbortController?.abort();
            activeAbortController = new AbortController();

            updateNavigationState(documentKey);
            updateDocumentTitle(documentKey);
            renderLoadingState(config);

            if (options.history === 'push') {
                window.history.pushState({ legalDocument: documentKey }, '', getCanonicalUrl(documentKey));
            } else if (options.history === 'replace') {
                window.history.replaceState({ legalDocument: documentKey }, '', getCanonicalUrl(documentKey, options.keepHash));
            }

            if (options.scrollToTop) {
                window.scrollTo({ top: 0, behavior: 'auto' });
            }

            const cleanup = await window.legalPageUtils.initLegalPage({
                ...config,
                payload: payloadCache.get(documentKey),
                signal: activeAbortController.signal,
                onPayload: (payload) => payloadCache.set(documentKey, payload),
                onLoaded: () => {
                    loadedSuccessfully = true;
                },
            });

            // A slower response from a previous navigation must never replace
            // the current document or own its event listeners.
            if (sequence !== navigationSequence) {
                if (typeof cleanup === 'function') cleanup();
                return;
            }

            activeCleanup = cleanup;
            if (!loadedSuccessfully) {
                return;
            }

            activeDocument = documentKey;
            updateDocumentTitle(documentKey);

            if (options.restoreHash && window.location.hash) {
                const target = document.getElementById(window.location.hash.slice(1));
                target?.scrollIntoView({ behavior: 'auto', block: 'start' });
            }

            if (options.focusContent) {
                document.getElementById('main-container')?.focus({ preventScroll: true });
            }
        }

        documentLinks.forEach((link) => {
            link.addEventListener('click', (event) => {
                const documentKey = link.dataset.legalDocument;
                if (!enabledDocuments.includes(documentKey)) return;

                event.preventDefault();
                if (documentKey === activeDocument) return;
                return showDocument(documentKey, {
                    history: 'push',
                    scrollToTop: true,
                    focusContent: true,
                });
            });
        });

        const handlePopState = () => {
            const requestedDocument = getRequestedDocument();
            const nextDocument = requestedDocument || enabledDocuments[0] || 'privacy';
            if (nextDocument === activeDocument) return;

            showDocument(nextDocument, {
                history: requestedDocument === nextDocument ? undefined : 'replace',
                scrollToTop: true,
                focusContent: true,
                keepHash: true,
                restoreHash: true,
            });
        };
        window.addEventListener('popstate', handlePopState);

        // Direct routes always win over link-visibility settings. Mandatory
        // notice and consent flows link to these routes even when an operator
        // has hidden the optional login-footer link.
        const initialDocument = requestedDocument || enabledDocuments[0] || 'privacy';
        const isCanonicalRequest = requestedDocument === initialDocument
            && window.location.pathname.replace(/\/+$/, '') === DOCUMENT_ROUTES[initialDocument]
            && !window.location.search;

        await showDocument(initialDocument, {
            history: isCanonicalRequest ? undefined : 'replace',
            keepHash: isCanonicalRequest,
            restoreHash: true,
        });

        const handleI18nUpdate = () => {
            if (activeDocument) {
                updateDocumentTitle(activeDocument);
            }
        };
        document.addEventListener('i18n:updated', handleI18nUpdate);

        window.addEventListener('pagehide', () => {
            navigationSequence += 1;
            activeAbortController?.abort();
            if (typeof activeCleanup === 'function') activeCleanup();
            document.removeEventListener('i18n:updated', handleI18nUpdate);
            window.removeEventListener('popstate', handlePopState);
        }, { once: true });
    });
})();
