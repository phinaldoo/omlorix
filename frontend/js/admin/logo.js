(function () {
    const headerLogoButton = document.querySelector('.admin-sidebar-logo');
    const headerLogoImg = headerLogoButton?.querySelector('img');
    const uploadLogoButtonLight = document.getElementById('uploadLogoButtonLight');
    const uploadLogoButtonDark = document.getElementById('uploadLogoButtonDark');
    const uploadIconButton = document.getElementById('uploadIconButton');
    const previewElements = {
        icon: document.getElementById('iconPreview'),
        light: document.getElementById('logoPreviewLight'),
        dark: document.getElementById('logoPreviewDark'),
    };
    const uploadButtons = {
        icon: uploadIconButton,
        light: uploadLogoButtonLight,
        dark: uploadLogoButtonDark,
    };

    if (!headerLogoImg && !uploadLogoButtonLight && !uploadLogoButtonDark && !uploadIconButton) {
        // Nothing to do if the related UI elements are missing.
        return;
    }

    const defaultLogoSrc = headerLogoImg?.getAttribute('src') || '';

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const formatT = (key, fallback, vars = {}) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        let text = t(key, fallback);
        Object.entries(vars).forEach(([name, value]) => {
            text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), value);
        });
        return text;
    };

    const state = {
        logos: {
            light: null,
            dark: null,
        },
        icon: null,
        objectUrls: new Map(),
        initialFetchComplete: false,
        loadPromise: null,
    };

    const notifyErrorSafe = (message) => {
        if (typeof notifyError === 'function') {
            notifyError(message);
        } else {
            console.error(message);
        }
    };

    const notifySuccessSafe = (message) => {
        if (typeof notifySuccess === 'function') {
            notifySuccess(message);
        }
    };

    /**
     * Toggle busy state for the asset upload cards without mutating their
     * internal markup. The generic admin loading helper rewrites button text,
     * which destroys the preview DOM inside these upload cards.
     *
     * @param {HTMLButtonElement | null | undefined} button
     * @param {boolean} isLoading
     */
    const setAssetUploadCardLoadingState = (button, isLoading) => {
        if (!button) {
            return;
        }

        button.disabled = Boolean(isLoading);
        button.classList.toggle('loading', Boolean(isLoading));

        if (isLoading) {
            button.setAttribute('aria-busy', 'true');
        } else {
            button.removeAttribute('aria-busy');
        }
    };

    const getEffectiveTheme = () => {
        const root = document.documentElement;
        const modeAttr = root.getAttribute('data-mode');
        if (modeAttr === 'dark' || modeAttr === 'light') {
            return modeAttr;
        }
        const themeAttr = root.getAttribute('data-theme');
        if (themeAttr === 'dark' || themeAttr === 'light') {
            return themeAttr;
        }
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return 'dark';
        }
        return 'light';
    };

    const normalizeMimeType = (value) => String(value || '').split(';')[0].trim().toLowerCase();

    /**
     * Build a logo asset URL with an optional cache-busting token.
     *
     * The backend ignores unknown query params, so we can safely attach a
     * version value after uploads to guarantee that the browser asks for the
     * newly written file even if the extension changed from PNG to SVG.
     *
     * @param {'light'|'dark'} theme
     * @param {string | number | null} [version]
     * @returns {string}
     */
    const buildLogoAssetUrl = (theme, version = null) => {
        const params = new URLSearchParams();
        params.set('theme', theme);
        if (version !== null && version !== undefined && version !== '') {
            params.set('v', String(version));
        }
        return `/api/v1/settings/logo/get?${params.toString()}`;
    };

    const renderInlineSvg = ({ container, svgText, altText, imageClassName }) => {
        if (!container || !svgText || typeof DOMParser !== 'function') {
            return false;
        }

        const parser = new DOMParser();
        const documentNode = parser.parseFromString(svgText, 'image/svg+xml');
        const parserError = documentNode.querySelector('parsererror');
        const svgElement = documentNode.documentElement;
        if (parserError || !svgElement || svgElement.localName !== 'svg') {
            return false;
        }

        const importedSvg = document.importNode(svgElement, true);
        importedSvg.classList.add('asset-upload-inline-svg');
        if (imageClassName) {
            importedSvg.classList.add(imageClassName);
        }
        importedSvg.setAttribute('role', 'img');
        if (altText) {
            importedSvg.setAttribute('aria-label', altText);
        } else {
            importedSvg.setAttribute('aria-hidden', 'true');
        }
        importedSvg.removeAttribute('width');
        importedSvg.removeAttribute('height');
        container.appendChild(importedSvg);
        return true;
    };

    const renderAssetContent = ({ container, entry, altText, imageClassName }) => {
        if (!container) {
            return false;
        }

        container.replaceChildren();

        if (entry?.svgText && normalizeMimeType(entry.type) === 'image/svg+xml') {
            if (renderInlineSvg({
                container,
                svgText: entry.svgText,
                altText,
                imageClassName,
            })) {
                return true;
            }
        }

        if (entry?.url) {
            // Local SVG selections remain inert until the backend has sanitized
            // them and the follow-up fetch gives us safe inlineable SVG text.
            const img = document.createElement('img');
            img.src = entry.url;
            img.alt = altText || '';
            if (imageClassName) {
                img.classList.add(imageClassName);
            }
            container.appendChild(img);
            return true;
        }

        return false;
    };

    const renderHeaderLogo = (entry) => {
        if (!headerLogoButton) {
            return;
        }

        const fallbackEntry = defaultLogoSrc ? { url: defaultLogoSrc } : null;
        renderAssetContent({
            container: headerLogoButton,
            entry: entry || fallbackEntry,
            altText: '',
            imageClassName: 'admin-sidebar-logo-svg',
        });
    };

    const applyHeaderLogo = () => {
        const theme = getEffectiveTheme();
        const preferred = state.logos[theme];
        const fallback = state.logos.light || state.logos.dark;
        renderHeaderLogo(preferred || fallback);
    };

    const revokeLogoUrl = (theme) => {
        const existingUrl = state.objectUrls.get(theme);
        if (existingUrl) {
            URL.revokeObjectURL(existingUrl);
            state.objectUrls.delete(theme);
        }
    };

    const getPreviewAltText = (key) => {
        switch (key) {
            case 'icon':
                return t('logo_preview_alt_icon', 'Uploaded app icon preview');
            case 'dark':
                return t('logo_preview_alt_dark', 'Uploaded dark theme logo preview');
            case 'light':
            default:
                return t('logo_preview_alt_light', 'Uploaded light theme logo preview');
        }
    };

    const updatePreviewElement = (key, entry) => {
        const previewElement = previewElements[key];
        if (!previewElement) {
            return;
        }

        const content = previewElement.querySelector('.asset-upload-preview-content');
        const placeholder = previewElement.querySelector('.asset-upload-placeholder');

        const hasPreview = renderAssetContent({
            container: content,
            entry,
            altText: getPreviewAltText(key),
        });

        if (hasPreview) {
            previewElement.setAttribute('data-has-preview', 'true');
            if (placeholder) {
                placeholder.setAttribute('hidden', '');
            }
        } else {
            previewElement.removeAttribute('data-has-preview');
            if (placeholder) {
                placeholder.removeAttribute('hidden');
            }
        }
    };

    const createAssetEntry = async (blob, contentType, { inlineSvg = false } = {}) => {
        if (!blob) {
            return null;
        }

        const normalizedType = normalizeMimeType(contentType) || normalizeMimeType(blob.type);
        const url = URL.createObjectURL(blob);
        const entry = {
            type: normalizedType,
            url,
        };

        if (inlineSvg && normalizedType === 'image/svg+xml' && typeof blob.text === 'function') {
            entry.svgText = await blob.text();
        }

        return entry;
    };

    const storeLogo = async (theme, blob, contentType, options = {}) => {
        revokeLogoUrl(theme);

        if (blob) {
            const assetEntry = await createAssetEntry(blob, contentType, options);
            if (assetEntry?.url) {
                state.objectUrls.set(theme, assetEntry.url);
            }
            state.logos[theme] = assetEntry;
        } else {
            state.logos[theme] = null;
        }

        updatePreviewElement(theme, state.logos[theme]);
        applyHeaderLogo();
    };

    const storeIcon = async (blob, contentType, options = {}) => {
        revokeLogoUrl('icon');

        if (blob) {
            const assetEntry = await createAssetEntry(blob, contentType, options);
            if (assetEntry?.url) {
                state.objectUrls.set('icon', assetEntry.url);
            }
            state.icon = assetEntry;
        } else {
            state.icon = null;
        }

        updatePreviewElement('icon', state.icon);
    };

    const buildErrorMessage = async (response, fallback) => {
        if (!response) {
            return fallback;
        }

        try {
            const data = await response.clone().json();
            if (typeof data === 'string' && data.trim()) {
                return data.trim();
            }
            if (data?.message) {
                return data.message;
            }
            if (data?.detail) {
                return data.detail;
            }
        } catch (jsonError) {
            // Ignore JSON parsing issues; fall back to text if available.
            try {
                const text = await response.clone().text();
                if (text.trim()) {
                    return text.trim();
                }
            } catch (textError) {
                // Ignore plain text parsing issues.
            }
        }

        return fallback;
    };

    /**
     * Fetch the persisted logo for a theme.
     *
     * @param {'light'|'dark'} theme
     * @param {{ version?: string | number | null }} [options]
     */
    const fetchLogoVariant = async (theme, options = {}) => {
        try {
            const response = await window.authedFetch(buildLogoAssetUrl(theme, options.version ?? null), {
                method: 'GET',
                headers: {
                    'Content-Type': null,
                },
                cache: 'no-cache',
            });

            if (response.status === 404) {
                await storeLogo(theme, null);
                return;
            }

            if (!response.ok) {
                notifyError(formatT('logo_load_error_status', 'Failed to load {theme} logo (status {status}).', {
                    theme,
                    status: response.status,
                }));
                return;
            }

            const blob = await response.blob();
            if (!blob || blob.size === 0) {
                await storeLogo(theme, null);
                return;
            }

            await storeLogo(theme, blob, response.headers.get('Content-Type'), { inlineSvg: true });
        } catch (error) {
            console.error(`Failed to load ${theme} logo`, error);
            if (state.initialFetchComplete) {
                notifyErrorSafe(error.message || formatT('logo_load_error_theme', 'Failed to load {theme} logo.', { theme }));
            }
        }
    };

    const fetchIconAsset = async () => {
        try {
            const response = await window.authedFetch('/api/v1/settings/icon/get', {
                method: 'GET',
                headers: {
                    'Content-Type': null,
                },
                cache: 'no-cache',
            });

            if (response.status === 404) {
                await storeIcon(null);
                return;
            }

            if (!response.ok) {
                notifyError(formatT('logo_icon_load_error_status', 'Failed to load icon (status {status}).', {
                    status: response.status,
                }));
                return;
            }

            const blob = await response.blob();
            if (!blob || blob.size === 0) {
                await storeIcon(null);
                return;
            }

            await storeIcon(blob, response.headers.get('Content-Type'), { inlineSvg: true });
        } catch (error) {
            console.error('Failed to load icon', error);
            if (state.initialFetchComplete) {
                notifyErrorSafe(error.message || t('logo_icon_load_error', 'Failed to load icon.'));
            }
        }
    };

    const loadAllLogos = async () => {
        if (!state.loadPromise) {
            state.loadPromise = Promise.all([
                fetchLogoVariant('light'),
                fetchLogoVariant('dark'),
                fetchIconAsset(),
            ]).finally(() => {
                state.loadPromise = null;
            });
        }

        await state.loadPromise;

        state.initialFetchComplete = true;
        applyHeaderLogo();
    };

    const observeThemeChanges = () => {
        const root = document.documentElement;
        const observer = new MutationObserver(() => applyHeaderLogo());
        observer.observe(root, {
            attributes: true,
            attributeFilter: ['data-mode', 'data-theme'],
        });

        if (window.matchMedia) {
            const media = window.matchMedia('(prefers-color-scheme: dark)');
            const handleSystemChange = () => {
                const mode = document.documentElement.getAttribute('data-mode');
                if (!mode || mode === 'system') {
                    applyHeaderLogo();
                }
            };
            try {
                media.addEventListener('change', handleSystemChange);
            } catch (error) {
                // Safari <14 fallback
                media.addListener(handleSystemChange);
            }
        }
    };

    /**
     * Build a persistent picker for each branding asset button.
     *
     * Keeping the native file input mounted avoids the intermittent browser
     * cases where a detached one-off input never reports its selection back.
     */
    const createBrandAssetPicker = (id) => {
        if (typeof window.createPersistentFilePicker === 'function') {
            return window.createPersistentFilePicker({
                id,
                accept: 'image/*,.svg',
            });
        }

        return null;
    };

    const assetPickers = {
        icon: createBrandAssetPicker('admin-brand-asset-picker-icon'),
        light: createBrandAssetPicker('admin-brand-asset-picker-light'),
        dark: createBrandAssetPicker('admin-brand-asset-picker-dark'),
    };

    /**
     * Upload a selected branding asset and report whether the request succeeded.
     *
     * Returning a boolean lets the caller decide whether it should keep an
     * optimistic preview or restore the last saved asset from the backend.
     *
     * @param {Object} options
     * @param {string} options.endpoint
     * @param {string} options.fieldName
     * @param {File|Blob|null} options.file
     * @param {string} options.successMessage
     * @param {string} options.errorMessage
     * @param {() => Promise<void>} [options.onSuccess]
     * @returns {Promise<boolean>}
     */
    const uploadMultipartAsset = async ({ endpoint, fieldName, file, successMessage, errorMessage, onSuccess }) => {
        if (!file) {
            return false;
        }

        try {
            const formData = new FormData();
            formData.append(fieldName, file);

            const response = await window.authedFetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': null,
                },
                body: formData,
            });

            if (!response.ok) {
                const message = await buildErrorMessage(response, errorMessage);
                notifyError(message);
                return false;
            }

            notifySuccessSafe(successMessage);
            if (typeof onSuccess === 'function') {
                await onSuccess();
            }
            return true;
        } catch (error) {
            console.error('File upload failed', error);
            notifyErrorSafe(error.message || errorMessage);
            return false;
        }
    };

    /**
     * Open the correct picker for a branding asset and upload the selected file.
     *
     * @param {'light'|'dark'|'icon'} assetKey
     * @param {Object} uploadConfig
     * @param {string} uploadConfig.endpoint
     * @param {string} uploadConfig.fieldName
     * @param {string} uploadConfig.successMessage
     * @param {string} uploadConfig.errorMessage
     * @param {(file: File) => Promise<void>} [uploadConfig.previewSelection]
     * @param {() => Promise<void>} [uploadConfig.onFailure]
     * @param {() => Promise<void>} uploadConfig.onSuccess
     */
    const handleAssetUpload = async (assetKey, uploadConfig) => {
        const picker = assetPickers[assetKey];
        const triggerButton = uploadButtons[assetKey];
        if (typeof picker?.open !== 'function') {
            notifyErrorSafe(uploadConfig.errorMessage);
            return;
        }

        const file = await picker?.open?.();
        if (!file) {
            return;
        }

        setAssetUploadCardLoadingState(triggerButton, true);
        try {
            if (typeof uploadConfig.previewSelection === 'function') {
                await uploadConfig.previewSelection(file);
            }

            const uploadSucceeded = await uploadMultipartAsset({
                ...uploadConfig,
                file,
            });
            if (!uploadSucceeded && typeof uploadConfig.onFailure === 'function') {
                await uploadConfig.onFailure();
            }
        } finally {
            setAssetUploadCardLoadingState(triggerButton, false);
        }
    };

    const handleLogoUpload = async (theme) => {
        await handleAssetUpload(theme, {
            endpoint: `/api/v1/settings/logo/upload?theme=${encodeURIComponent(theme)}`,
            fieldName: 'logo',
            successMessage: formatT('logo_upload_success', 'Uploaded {theme} logo successfully.', { theme }),
            errorMessage: formatT('logo_upload_error', 'Failed to upload {theme} logo.', { theme }),
            // Show the selected file immediately so the preview stays correct
            // even before the backend roundtrip finishes reading the new asset.
            previewSelection: (file) => storeLogo(theme, file, file.type),
            // If the upload fails, restore the last saved asset from storage.
            onFailure: () => fetchLogoVariant(theme),
            // Force a fresh fetch after success so browsers do not reuse the
            // previous PNG/SVG response when the underlying extension changed.
            onSuccess: () => fetchLogoVariant(theme, { version: Date.now() }),
        });
    };

    const handleIconUpload = async () => {
        await handleAssetUpload('icon', {
            endpoint: '/api/v1/settings/icon/upload',
            fieldName: 'icon',
            successMessage: t('logo_icon_upload_success', 'Icon uploaded successfully.'),
            errorMessage: t('logo_icon_upload_error', 'Failed to upload icon.'),
            // Show the selected icon immediately so the upload button reflects
            // the user's choice even before the backend roundtrip completes.
            previewSelection: (file) => storeIcon(file, file.type),
            // If the upload fails, reload the last saved icon so the UI does
            // not keep showing an unsaved preview.
            onFailure: () => fetchIconAsset(),
            onSuccess: () => fetchIconAsset(),
        });
    };

    const bindUploadButtons = () => {
        uploadLogoButtonLight?.addEventListener('click', () => handleLogoUpload('light'));
        uploadLogoButtonDark?.addEventListener('click', () => handleLogoUpload('dark'));
        uploadIconButton?.addEventListener('click', handleIconUpload);
    };

    const cleanupObjectUrls = () => {
        state.objectUrls.forEach((url) => URL.revokeObjectURL(url));
        state.objectUrls.clear();
        Object.values(assetPickers).forEach((picker) => picker?.destroy?.());
    };

    bindUploadButtons();
    observeThemeChanges();
    // admin/init.js owns initial loading so it can keep the admin shell hidden
    // until branding assets are ready. Do not auto-fetch here; otherwise the
    // startup call in init.js loads icon and logo assets a second time.
    window.loadAllLogos = loadAllLogos;

    window.addEventListener('beforeunload', cleanupObjectUrls, { once: true });
})();
