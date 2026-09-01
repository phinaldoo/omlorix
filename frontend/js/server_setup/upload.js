// Upload handling for server_setup

const SERVER_SETUP_ALLOWED_IMAGE_TYPES = new Set([
    'image/png',
    'image/jpeg',
    'image/webp',
    'image/svg+xml'
]);
const SERVER_SETUP_ALLOWED_IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'svg']);
const SERVER_SETUP_LOGO_MAX_BYTES = 5 * 1024 * 1024;
const SERVER_SETUP_ICON_MAX_BYTES = 10 * 1024 * 1024;

function initializeUploads() {
    const logoLightUpload = document.getElementById('logoLightUpload');
    const logoLightInput = document.getElementById('logoLightInput');
    const logoLightPreview = document.getElementById('logoLightPreview');

    if (logoLightUpload && logoLightInput) {
        logoLightUpload.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            logoLightInput.click();
        });
        bindUploadKeyboardTrigger(logoLightUpload, logoLightInput);
        logoLightInput.addEventListener('change', (event) => handleLogoSelection(event, 'light', logoLightPreview));
    }

    const logoDarkUpload = document.getElementById('logoDarkUpload');
    const logoDarkInput = document.getElementById('logoDarkInput');
    const logoDarkPreview = document.getElementById('logoDarkPreview');

    if (logoDarkUpload && logoDarkInput) {
        logoDarkUpload.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            logoDarkInput.click();
        });
        bindUploadKeyboardTrigger(logoDarkUpload, logoDarkInput);
        logoDarkInput.addEventListener('change', (event) => handleLogoSelection(event, 'dark', logoDarkPreview));
    }

    const iconUpload = document.getElementById('iconUpload');
    const iconInput = document.getElementById('iconInput');
    const iconPreview = document.getElementById('iconPreview');

    if (iconUpload && iconInput) {
        iconUpload.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            iconInput.click();
        });
        bindUploadKeyboardTrigger(iconUpload, iconInput);
        iconInput.addEventListener('change', (event) => handleIconSelection(event, iconPreview));
    }
}

function bindUploadKeyboardTrigger(trigger, input) {
    if (!trigger || !input) {
        return;
    }

    trigger.addEventListener('keydown', (event) => {
        const key = event.key;
        if (key !== 'Enter' && key !== ' ') {
            return;
        }

        event.preventDefault();
        input.click();
    });
}

function getServerSetupTranslation(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function setServerSetupTranslatedAttribute(element, attribute, translationKey, fallback) {
    if (!element) {
        return;
    }

    element.setAttribute(attribute, getServerSetupTranslation(translationKey, fallback));
    element.setAttribute('data-i18n-attr', `${attribute}:${translationKey}`);
}

function formatServerSetupTranslation(key, fallback, values = {}) {
    const template = getServerSetupTranslation(key, fallback);
    return String(template).replace(/\{(\w+)\}/g, (_, token) => {
        const value = values[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function getFileExtension(file) {
    const name = typeof file?.name === 'string' ? file.name : '';
    const parts = name.toLowerCase().split('.');
    return parts.length > 1 ? parts.pop() : '';
}

function validateSelectedImage(file, { allowSvg = true, maxBytes, label }) {
    if (!file) {
        return {
            valid: false,
            message: formatServerSetupTranslation(
                'error_brand_asset_required',
                '{label} is required.',
                { label }
            )
        };
    }

    const extension = getFileExtension(file);
    const hasRecognizedType = !file.type || SERVER_SETUP_ALLOWED_IMAGE_TYPES.has(file.type);
    const hasRecognizedExtension = !extension || SERVER_SETUP_ALLOWED_IMAGE_EXTENSIONS.has(extension);

    if (!hasRecognizedType || !hasRecognizedExtension) {
        return {
            valid: false,
            message: formatServerSetupTranslation(
                allowSvg ? 'error_brand_asset_image_types_svg' : 'error_brand_asset_image_types',
                allowSvg
                    ? '{label} must be a PNG, JPEG, WEBP, or SVG image.'
                    : '{label} must be a PNG, JPEG, or WEBP image.',
                { label }
            )
        };
    }

    if (!allowSvg && extension === 'svg') {
        return {
            valid: false,
            message: formatServerSetupTranslation(
                'error_brand_asset_image_types',
                '{label} must be a PNG, JPEG, or WEBP image.',
                { label }
            )
        };
    }

    if (file.size > maxBytes) {
        return {
            valid: false,
            message: formatServerSetupTranslation(
                'error_brand_asset_max_size',
                '{label} must be {size} MB or smaller.',
                { label, size: Math.floor(maxBytes / (1024 * 1024)) }
            )
        };
    }

    return { valid: true };
}

function clearUploadInput(input) {
    if (input) {
        input.value = '';
    }
}

function showImagePreview(
    file,
    previewElement,
    translationKey,
    fallbackAlt,
    changeLabelKey,
    changeLabelFallback
) {
    if (!previewElement || !file) {
        return;
    }

    const reader = new FileReader();
    reader.onload = (event) => {
        const img = document.createElement('img');
        img.src = event.target.result;
        setServerSetupTranslatedAttribute(img, 'alt', translationKey, fallbackAlt);
        previewElement.textContent = '';
        previewElement.appendChild(img);
        previewElement.classList.add('has-image');
        setServerSetupTranslatedAttribute(
            previewElement.closest('.upload-area'),
            'aria-label',
            changeLabelKey,
            changeLabelFallback
        );
    };
    reader.readAsDataURL(file);
}

function buildPreviewUrl(url) {
    try {
        const resolved = new URL(url, window.location.origin);
        resolved.searchParams.set('cb', String(Date.now()));
        return resolved.toString();
    } catch (_error) {
        const separator = String(url).includes('?') ? '&' : '?';
        return `${url}${separator}cb=${Date.now()}`;
    }
}

function showImagePreviewFromUrl(
    url,
    previewElement,
    translationKey,
    fallbackAlt,
    changeLabelKey,
    changeLabelFallback
) {
    if (!previewElement || !url) {
        return;
    }

    const img = document.createElement('img');
    img.src = buildPreviewUrl(url);
    setServerSetupTranslatedAttribute(img, 'alt', translationKey, fallbackAlt);
    previewElement.textContent = '';
    previewElement.appendChild(img);
    previewElement.classList.add('has-image');
    setServerSetupTranslatedAttribute(
        previewElement.closest('.upload-area'),
        'aria-label',
        changeLabelKey,
        changeLabelFallback
    );
}

function applySavedBrandingAsset(
    key,
    asset,
    previewElement,
    translationKey,
    fallbackAlt,
    changeLabelKey,
    changeLabelFallback
) {
    if (!asset || !previewElement) {
        return;
    }

    state.serverData[key] = {
        ...asset,
        source: 'existing',
    };
    showImagePreviewFromUrl(
        asset.url,
        previewElement,
        translationKey,
        fallbackAlt,
        changeLabelKey,
        changeLabelFallback
    );
}

async function loadSavedBrandingAssets() {
    const fetchImpl = typeof window.authedFetch === 'function'
        ? window.authedFetch.bind(window)
        : window.fetch.bind(window);

    try {
        const response = await fetchImpl('/api/v1/settings/branding/assets', {
            method: 'GET',
            cache: 'no-cache'
        });

        if (!response.ok) {
            throw new Error(`Failed to load saved branding assets (status ${response.status}).`);
        }

        const data = await response.json();
        applySavedBrandingAsset(
            'logoLight',
            data?.logos?.light,
            document.getElementById('logoLightPreview'),
            'logo_preview_alt',
            'Logo preview',
            'change_logo_light_aria',
            'Change light theme logo'
        );
        applySavedBrandingAsset(
            'logoDark',
            data?.logos?.dark,
            document.getElementById('logoDarkPreview'),
            'logo_preview_alt',
            'Logo preview',
            'change_logo_dark_aria',
            'Change dark theme logo'
        );
        applySavedBrandingAsset(
            'icon',
            data?.icon,
            document.getElementById('iconPreview'),
            'icon_preview_alt',
            'Icon preview',
            'change_icon_aria',
            'Change app icon'
        );

        if (typeof handleBrandingAssetsUpdated === 'function') {
            handleBrandingAssetsUpdated();
        }
    } catch (error) {
        console.error('Failed to preload saved branding assets', error);
    }
}

function handleLogoSelection(event, theme, previewElement) {
    const input = event.target;
    const file = input?.files?.[0];
    if (!file) {
        return;
    }

    const validation = validateSelectedImage(file, {
        allowSvg: true,
        maxBytes: SERVER_SETUP_LOGO_MAX_BYTES,
        label: getServerSetupTranslation('brand_asset_logo_label', 'Logo')
    });
    if (!validation.valid) {
        clearUploadInput(input);
        if (typeof notifyError === 'function') {
            notifyError(validation.message);
        }
        return;
    }

    const changeLabelKey = theme === 'light'
        ? 'change_logo_light_aria'
        : 'change_logo_dark_aria';
    const changeLabelFallback = theme === 'light'
        ? 'Change light theme logo'
        : 'Change dark theme logo';
    showImagePreview(
        file,
        previewElement,
        'logo_preview_alt',
        'Logo preview',
        changeLabelKey,
        changeLabelFallback
    );

    if (theme === 'light') {
        state.serverData.logoLight = file;
    } else {
        state.serverData.logoDark = file;
    }

    if (typeof handleBrandingAssetsUpdated === 'function') {
        handleBrandingAssetsUpdated();
    }
}

function handleIconSelection(event, previewElement) {
    const input = event.target;
    const file = input?.files?.[0];
    if (!file) {
        return;
    }

    const validation = validateSelectedImage(file, {
        allowSvg: true,
        maxBytes: SERVER_SETUP_ICON_MAX_BYTES,
        label: getServerSetupTranslation('brand_asset_icon_label', 'Icon')
    });
    if (!validation.valid) {
        clearUploadInput(input);
        if (typeof notifyError === 'function') {
            notifyError(validation.message);
        }
        return;
    }

    showImagePreview(
        file,
        previewElement,
        'icon_preview_alt',
        'Icon preview',
        'change_icon_aria',
        'Change app icon'
    );
    state.serverData.icon = file;
}

async function uploadBrandingAssets() {
    const uploads = [];

    if (state.serverData.logoLight instanceof File) {
        uploads.push(uploadSingleBrandingAsset({
            endpoint: '/api/v1/settings/logo/upload?theme=light',
            fieldName: 'logo',
            file: state.serverData.logoLight,
            failureMessage: getServerSetupTranslation('error_logo_upload_failed', 'Failed to upload the light logo.')
        }));
    }

    if (state.serverData.logoDark instanceof File) {
        uploads.push(uploadSingleBrandingAsset({
            endpoint: '/api/v1/settings/logo/upload?theme=dark',
            fieldName: 'logo',
            file: state.serverData.logoDark,
            failureMessage: getServerSetupTranslation('error_logo_upload_failed', 'Failed to upload the dark logo.')
        }));
    }

    if (state.serverData.icon instanceof File) {
        uploads.push(uploadSingleBrandingAsset({
            endpoint: '/api/v1/settings/icon/upload',
            fieldName: 'icon',
            file: state.serverData.icon,
            failureMessage: getServerSetupTranslation('error_icon_upload_failed', 'Failed to upload the icon.')
        }));
    }

    if (uploads.length === 0) {
        return;
    }

    await Promise.all(uploads);
}

async function uploadSingleBrandingAsset({ endpoint, fieldName, file, failureMessage }) {
    const formData = new FormData();
    formData.append(fieldName, file);

    const response = await window.authedFetch(endpoint, {
        method: 'POST',
        body: formData
    });

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const detail = typeof errorData.detail === 'string' ? errorData.detail : failureMessage;
        throw new Error(detail || failureMessage);
    }
}

window.uploadBrandingAssets = uploadBrandingAssets;
window.loadSavedBrandingAssets = loadSavedBrandingAssets;
