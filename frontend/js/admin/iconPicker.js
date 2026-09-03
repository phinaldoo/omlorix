/**
 * Icon Picker Module for Model Icon selection
 * Provides a visual icon picker with preset model icons and custom SVG support
 */
(function () {
    if (window.IconPicker) {
        return;
    }

    // Define available icons from Icons.js grouped by usage
    const PROVIDER_ICON_KEYS = Array.isArray(window.DEFAULT_PROVIDER_ICON_KEYS)
        ? [...window.DEFAULT_PROVIDER_ICON_KEYS]
        : [
            'openai',
            'anthropic',
            'google_aistudio',
            'ollama',
            'openrouter',
            'nvidia',
            'mistral',
            'meta',
            'xai',
            'amazon',
            'microsoft',
            'minimax',
            'lmstudio',
            'elevenlabs',
            'nebius',
        ];

    const MODEL_ONLY_ICON_KEYS = [
        'claude',
        'gemini',
        'gemma',
        'deepseek',
        'grok',
        'kimi',
        'qwen',
        'omlorix',
    ];

    const ICON_PRESET_TYPES = {
        provider: PROVIDER_ICON_KEYS,
        model: [...PROVIDER_ICON_KEYS, ...MODEL_ONLY_ICON_KEYS],
    };

    const t = (key, fallback) => {
        if (typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback !== undefined ? fallback : key;
    };

    const ICON_IMAGE_MAX_SIZE = 96;
    const ICON_IMAGE_OUTPUT_QUALITY = 0.82;

    const sanitizeSvgMarkup = (svg) => {
        const source = String(svg || '');
        if (!source.trim()) {
            return '';
        }
        if (window.ChatSanitizer?.sanitizeSvg) {
            return window.ChatSanitizer.sanitizeSvg(source);
        }
        if (window.DOMPurify?.sanitize) {
            return window.DOMPurify.sanitize(source, {
                USE_PROFILES: { svg: true },
                FORBID_ATTR: ['style', 'srcdoc'],
                ALLOW_DATA_ATTR: false,
            });
        }
        return '';
    };

    const setIconContainerMarkup = (container, markup) => {
        if (!container) {
            return;
        }
        const safe = String(markup || '');
        container.innerHTML = safe || `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
    };

    const setImagePreviewMarkup = (container, src, alt) => {
        if (!container) {
            return;
        }
        container.replaceChildren();
        const img = document.createElement('img');
        img.className = 'icon-picker-image';
        const normalizedSrc = typeof src === 'string' ? src.trim() : '';
        if (normalizedSrc) {
            img.src = normalizedSrc;
        }
        img.alt = String(alt || '');
        img.loading = 'lazy';
        img.decoding = 'async';
        img.style.display = 'block';
        // Image-backed icons are a shared visual primitive. Keep the crop and
        // mask on the image itself so every consumer (admin rows, model menus,
        // BYOK, agents, automations, and MCP cards) gets the same result.
        img.style.aspectRatio = '1';
        img.style.objectFit = 'cover';
        img.style.borderRadius = '50%';
        container.appendChild(img);
    };

    /**
     * Safely resolve the Icons map regardless of how it's declared
     * @returns {Record<string, string>}
     */
    const getIconsMap = () => {
        if (typeof Icons !== 'undefined') {
            return Icons;
        }
        if (typeof window !== 'undefined' && window.Icons) {
            return window.Icons;
        }
        return {};
    };

    const getPresetIconMarkup = (key, presetType = 'provider', iconsMap = getIconsMap()) => {
        const iconKey = presetType === 'model' ? 'omlorixModel' : key;
        const markup = iconsMap[iconKey];
        return typeof markup === 'string' ? markup : '';
    };

    /**
     * Get available icons from the global Icons object
     * @returns {Array<{key: string, svg: string, label: string}>}
     */
    const getAvailableIcons = (presetType = 'model', presetKeys = null) => {
        const iconsMap = getIconsMap();
        const presetKey = ICON_PRESET_TYPES[presetType] ? presetType : 'model';
        const requestedKeys = Array.isArray(presetKeys) && presetKeys.length
            ? presetKeys
            : ICON_PRESET_TYPES[presetKey];
        return requestedKeys
            .filter(key => iconsMap[key] && typeof iconsMap[key] === 'string')
            .map(key => ({
                key,
                svg: ensureUniqueSvgIds(
                    getPresetIconMarkup(key, presetKey, iconsMap),
                    `preset-${key}`,
                ),
                label: formatIconLabel(key),
            }));
    };

    /**
     * Format icon key into a human-readable label
     * @param {string} key 
     * @returns {string}
     */
    const formatIconLabel = (key) => {
        const fallback = key
            .replace(/_/g, ' ')
            .replace(/([A-Z])/g, ' $1')
            .trim()
            .split(' ')
            .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
            .join(' ');
        // Product and provider names naturally fall back to their formatted key,
        // while generic device presets can supply localized labels.
        return t(`icon_picker_icon_${key}`, fallback);
    };

    /**
     * Check if a string is a valid SVG
     * @param {string} value 
     * @returns {boolean}
     */
    const isValidSvg = (value) => {
        if (typeof value !== 'string') return false;
        const trimmed = value.trim();
        return trimmed.startsWith('<svg') && trimmed.includes('</svg>');
    };

    /**
     * Check whether an icon value is an image source.
     * Supports compact data URLs as well as standard image URLs.
     * @param {string} value
     * @returns {boolean}
     */
    const isImageIconValue = (value) => {
        if (typeof value !== 'string') return false;
        const trimmed = value.trim();
        if (!trimmed) return false;
        if (/^data:image\//i.test(trimmed)) return true;
        if (/^(https?:\/\/|\/|\.\/|\.\.\/)/i.test(trimmed)) {
            return /\.(png|jpe?g|gif|webp|svg)([?#].*)?$/i.test(trimmed) || /^data:image\//i.test(trimmed);
        }
        return /\.(png|jpe?g|gif|webp|svg)([?#].*)?$/i.test(trimmed);
    };

    const escapeHtmlAttribute = (value) => String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    const renderImageMarkup = (src, alt = '') => `
        <img
            class="icon-picker-image"
            src="${escapeHtmlAttribute(src)}"
            alt="${escapeHtmlAttribute(alt)}"
            loading="lazy"
            decoding="async"
            style="display:block;aspect-ratio:1;object-fit:cover;border-radius:50%;"
        >
    `;

    /**
     * Resolve current icon value to determine if it's a preset or custom
     * @param {string} value 
     * @returns {{type: 'preset'|'custom'|'image'|'empty', key?: string, svg?: string, src?: string}}
     */
    const resolveIconValue = (value) => {
        if (!value || (typeof value === 'string' && !value.trim())) {
            return { type: 'empty' };
        }
        const trimmed = value.trim();
        if (trimmed.startsWith('{')) {
            try {
                const parsed = JSON.parse(trimmed);
                if (parsed && typeof parsed === 'object') {
                    if (typeof parsed.image === 'string' && isImageIconValue(parsed.image.trim())) {
                        return { type: 'image', src: parsed.image.trim() };
                    }
                    if (typeof parsed.src === 'string' && isImageIconValue(parsed.src.trim())) {
                        return { type: 'image', src: parsed.src.trim() };
                    }
                    if (typeof parsed.svg === 'string') {
                        const sanitized = sanitizeSvgMarkup(parsed.svg.trim());
                        return { type: 'custom', svg: ensureUniqueSvgIds(sanitized) };
                    }
                }
            } catch (_) {
                // Fall back to raw string handling below.
            }
        }
        // Check if it's a raw SVG
        if (trimmed.startsWith('<')) {
            const sanitized = sanitizeSvgMarkup(trimmed);
            return { type: 'custom', svg: ensureUniqueSvgIds(sanitized) };
        }
        if (isImageIconValue(trimmed)) {
            return { type: 'image', src: trimmed };
        }
        // Check if it's a preset key
        const iconsMap = getIconsMap();
        if (iconsMap[trimmed]) {
            return { type: 'preset', key: trimmed };
        }
        // Treat as custom if not found
        return { type: 'custom', svg: '' };
    };

    /**
     * Sanitize icon values before persisting/sending to backend.
     * Ensures custom SVGs use single quotes to avoid JSON escaping issues.
     * @param {string} value
     * @returns {string}
     */
    const sanitizeIconValue = (value) => {
        if (typeof value !== 'string') {
            return value;
        }
        const trimmed = value.trim();
        if (!trimmed) {
            return '';
        }
        if (!trimmed.startsWith('<')) {
            return trimmed;
        }
        return trimmed.replace(/"/g, "'");
    };

    const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    let svgIdCounter = 0;
    let iconPickerInstanceCounter = 0;

    /**
     * Ensure any SVG IDs are made unique to prevent duplicate ID collisions when
     * rendering the same SVG multiple times (e.g., Gemini icon in picker grid).
     * @param {string} svg
     * @param {string} context
     * @returns {string}
     */
    const ensureUniqueSvgIds = (svg, context = 'svg') => {
        if (typeof svg !== 'string' || svg.indexOf('id=') === -1) {
            return svg;
        }

        const suffix = `${context}-${svgIdCounter++}`;
        const ids = new Set();
        const idRegex = /id=(['"])([^"']+)\1/g;
        let match;

        while ((match = idRegex.exec(svg)) !== null) {
            ids.add(match[2]);
        }

        if (!ids.size) {
            return svg;
        }

        let updated = svg;
        ids.forEach((id) => {
            const newId = `${id}-${suffix}`;
            const escapedId = escapeRegExp(id);
            const idDouble = new RegExp(`id="${escapedId}"`, 'g');
            const idSingle = new RegExp(`id='${escapedId}'`, 'g');
            updated = updated.replace(idDouble, `id="${newId}"`);
            updated = updated.replace(idSingle, `id='${newId}'`);
            const urlPattern = new RegExp(`url\\(#${escapedId}\\)`, 'g');
            updated = updated.replace(urlPattern, `url(#${newId})`);
            const hashDouble = new RegExp(`"#${escapedId}"`, 'g');
            const hashSingle = new RegExp(`'#${escapedId}'`, 'g');
            updated = updated.replace(hashDouble, `"#${newId}"`);
            updated = updated.replace(hashSingle, `'#${newId}'`);
            const hrefPattern = new RegExp(`(xlink:href|href)=(["'])#${escapedId}(["'])`, 'g');
            updated = updated.replace(hrefPattern, (_, attr, startQuote, endQuote) => `${attr}=${startQuote}#${newId}${endQuote}`);
        });

        return updated;
    };

    const renderIconMarkup = (value, options = {}) => {
        const {
            fallback = '',
            imageAlt = t('icon_picker_uploaded_image', 'Uploaded icon image'),
            presetType = 'provider',
        } = options;
        const resolved = resolveIconValue(value);
        const iconsMap = getIconsMap();

        if (resolved.type === 'preset' && iconsMap[resolved.key]) {
            return ensureUniqueSvgIds(
                getPresetIconMarkup(resolved.key, presetType, iconsMap),
                `markup-${resolved.key}`,
            );
        }
        if (resolved.type === 'custom' && resolved.svg) {
            return ensureUniqueSvgIds(sanitizeSvgMarkup(resolved.svg), 'markup-custom');
        }
        if (resolved.type === 'image' && resolved.src) {
            return renderImageMarkup(resolved.src, imageAlt);
        }
        // A fallback can itself be a complex SVG preset. Keep it collision-safe
        // just like the primary value instead of returning its raw fixed IDs.
        return fallback ? ensureUniqueSvgIds(fallback, 'markup-fallback') : '';
    };

    const renderModelIconMarkup = (value, options = {}) => {
        const iconsMap = getIconsMap();
        const fallback = Object.prototype.hasOwnProperty.call(options, 'fallback')
            ? options.fallback
            : (iconsMap.omlorixModel || iconsMap.omlorix || '');
        return renderIconMarkup(value, {
            ...options,
            fallback,
            presetType: 'model',
        });
    };

    const loadImageElement = (url) => new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error(t('icon_picker_image_load_failed', 'Failed to load image.')));
        image.decoding = 'async';
        image.src = url;
    });

    /**
     * Return the centered square source rectangle used for an icon crop.
     * Keeping this calculation separate makes portrait and landscape handling
     * deterministic and lets callers reuse the exact same crop semantics.
     *
     * @param {number} sourceWidth
     * @param {number} sourceHeight
     * @returns {{x: number, y: number, size: number}}
     */
    const calculateSquareCrop = (sourceWidth, sourceHeight) => {
        const width = Math.max(1, Number(sourceWidth) || 1);
        const height = Math.max(1, Number(sourceHeight) || 1);
        const size = Math.min(width, height);
        return {
            x: (width - size) / 2,
            y: (height - size) / 2,
            size,
        };
    };

    const compressIconImage = async (file) => {
        if (!(file instanceof File)) {
            throw new Error(t('icon_picker_image_missing', 'No image file selected.'));
        }

        if (!String(file.type || '').startsWith('image/')) {
            throw new Error(t('icon_picker_image_invalid_type', 'Please choose an image file.'));
        }

        const objectUrl = URL.createObjectURL(file);
        try {
            const image = await loadImageElement(objectUrl);
            const width = Math.max(1, Number(image.naturalWidth || image.width || 1));
            const height = Math.max(1, Number(image.naturalHeight || image.height || 1));
            const crop = calculateSquareCrop(width, height);
            const canvas = document.createElement('canvas');
            canvas.width = ICON_IMAGE_MAX_SIZE;
            canvas.height = ICON_IMAGE_MAX_SIZE;

            const context = canvas.getContext('2d', { alpha: true });
            if (!context) {
                throw new Error(t('icon_picker_image_canvas_failed', 'Could not prepare the icon image.'));
            }

            context.clearRect(0, 0, canvas.width, canvas.height);
            context.imageSmoothingEnabled = true;
            context.imageSmoothingQuality = 'high';

            // Persist a real square crop instead of letterboxing portrait or
            // landscape artwork with transparent padding. The circular clip is
            // also baked into the generated asset, while CSS repeats the mask
            // defensively for imported and external image values.
            context.save();
            context.beginPath();
            context.arc(
                canvas.width / 2,
                canvas.height / 2,
                canvas.width / 2,
                0,
                Math.PI * 2
            );
            context.clip();
            context.drawImage(
                image,
                crop.x,
                crop.y,
                crop.size,
                crop.size,
                0,
                0,
                canvas.width,
                canvas.height
            );
            context.restore();

            let output = canvas.toDataURL('image/webp', ICON_IMAGE_OUTPUT_QUALITY);
            if (!/^data:image\/webp/i.test(output)) {
                output = canvas.toDataURL('image/png');
            }
            return output;
        } finally {
            URL.revokeObjectURL(objectUrl);
        }
    };

    /**
     * Create the icon picker control
     * @param {Object} options
     * @param {string} options.value - Current icon value
     * @param {Function} options.onChange - Callback when value changes
     * @returns {{container: HTMLElement, getValue: Function, setValue: Function}}
     */
    const createIconPicker = (options = {}) => {
        const {
            value = '',
            presetType = 'model',
            presetKeys = null,
            allowCustomSvg = true,
            allowImage = true,
            onChange,
        } = options;
        const availableIcons = getAvailableIcons(presetType, presetKeys);
        const availablePresetKeys = new Set(availableIcons.map((icon) => icon.key));

        /**
         * Keep picker state within the capabilities of this picker instance.
         * This also protects callers that assign a value without opening a tab.
         */
        const normalizeAllowedValue = (candidate) => {
            const sanitized = sanitizeIconValue(candidate || '');
            if (!sanitized) return '';
            const candidateResolved = resolveIconValue(sanitized);
            if (
                candidateResolved.type === 'preset'
                && availablePresetKeys.has(candidateResolved.key)
            ) {
                return candidateResolved.key;
            }
            if (
                candidateResolved.type === 'custom'
                && allowCustomSvg
                && candidateResolved.svg
                && isValidSvg(candidateResolved.svg)
            ) {
                return sanitized;
            }
            if (
                candidateResolved.type === 'image'
                && allowImage
                && candidateResolved.src
            ) {
                return candidateResolved.src;
            }
            return '';
        };

        const initialValue = normalizeAllowedValue(value);
        const resolved = resolveIconValue(initialValue);

        // State
        let currentValue = initialValue;
        const requestedTab = resolved.type === 'custom'
            ? 'custom'
            : (resolved.type === 'image' ? 'image' : 'preset');
        const allowedTabs = [
            'preset',
            ...(allowCustomSvg ? ['custom'] : []),
            ...(allowImage ? ['image'] : []),
        ];
        let activeTab = allowedTabs.includes(requestedTab) ? requestedTab : 'preset';
        const pickerInstanceId = `icon-picker-${++iconPickerInstanceCounter}`;

        // Main container
        const container = document.createElement('div');
        container.className = 'icon-picker';

        // Mode tabs
        const tabsContainer = document.createElement('div');
        tabsContainer.className = 'icon-picker-tabs';
        tabsContainer.setAttribute('role', 'tablist');

        const presetTab = document.createElement('button');
        presetTab.type = 'button';
        presetTab.className = 'icon-picker-tab' + (activeTab === 'preset' ? ' active' : '');
        presetTab.textContent = t('icon_picker_preset_tab', 'Preset Icons');
        presetTab.setAttribute('data-tab', 'preset');

        const customTab = document.createElement('button');
        customTab.type = 'button';
        customTab.className = 'icon-picker-tab' + (activeTab === 'custom' ? ' active' : '');
        customTab.textContent = t('icon_picker_custom_tab', 'Custom SVG');
        customTab.setAttribute('data-tab', 'custom');

        const imageTab = document.createElement('button');
        imageTab.type = 'button';
        imageTab.className = 'icon-picker-tab' + (activeTab === 'image' ? ' active' : '');
        imageTab.textContent = t('icon_picker_image_tab', 'Image');
        imageTab.setAttribute('data-tab', 'image');

        // Expose the segmented mode switcher as a single keyboard-navigable tablist.
        const tabs = [
            presetTab,
            ...(allowCustomSvg ? [customTab] : []),
            ...(allowImage ? [imageTab] : []),
        ];
        tabs.forEach((tab) => {
            const isActive = tab.getAttribute('data-tab') === activeTab;
            const tabName = tab.getAttribute('data-tab');
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-selected', String(isActive));
            tab.id = `${pickerInstanceId}-tab-${tabName}`;
            tab.setAttribute('aria-controls', `${pickerInstanceId}-panel-${tabName}`);
            tab.tabIndex = isActive ? 0 : -1;
        });

        tabsContainer.appendChild(presetTab);
        if (allowCustomSvg) tabsContainer.appendChild(customTab);
        if (allowImage) tabsContainer.appendChild(imageTab);
        container.appendChild(tabsContainer);

        // Content panels
        const contentContainer = document.createElement('div');
        contentContainer.className = 'icon-picker-content';

        // Preset icons panel
        const presetPanel = document.createElement('div');
        presetPanel.className = 'icon-picker-panel icon-picker-preset-panel' + (activeTab === 'preset' ? ' active' : '');
        presetPanel.setAttribute('data-panel', 'preset');

        const iconGrid = document.createElement('div');
        iconGrid.className = 'icon-picker-grid';

        availableIcons.forEach(({ key, svg, label }) => {
            const iconButton = document.createElement('button');
            iconButton.type = 'button';
            iconButton.className = 'icon-picker-icon-btn';
            iconButton.title = label;
            iconButton.setAttribute('data-icon-key', key);

            const isInitiallySelected = resolved.type === 'preset' && resolved.key === key;
            iconButton.setAttribute('aria-pressed', String(isInitiallySelected));
            if (isInitiallySelected) {
                iconButton.classList.add('selected');
            }

            const iconPreview = document.createElement('span');
            iconPreview.className = 'icon-picker-icon-preview';
            setIconContainerMarkup(iconPreview, ensureUniqueSvgIds(svg, `grid-${key}`));

            const iconLabel = document.createElement('span');
            iconLabel.className = 'icon-picker-icon-label';
            iconLabel.textContent = label;

            iconButton.appendChild(iconPreview);
            iconButton.appendChild(iconLabel);

            iconButton.addEventListener('click', () => {
                selectPresetIcon(key);
            });

            iconGrid.appendChild(iconButton);
        });

        presetPanel.appendChild(iconGrid);

        // Custom SVG panel
        const customPanel = document.createElement('div');
        customPanel.className = 'icon-picker-panel icon-picker-custom-panel' + (activeTab === 'custom' ? ' active' : '');
        customPanel.setAttribute('data-panel', 'custom');

        const customInputWrapper = document.createElement('div');
        customInputWrapper.className = 'icon-picker-custom-wrapper';

        // Keep the editor and its related action together as one logical
        // column. This prevents the Clear button from appearing detached
        // below the two-column editor/preview card.
        const customControls = document.createElement('div');
        customControls.className = 'icon-picker-control-stack icon-picker-custom-controls';

        const customTextarea = document.createElement('textarea');
        customTextarea.className = 'icon-picker-custom-input input';
        customTextarea.placeholder = t('icon_picker_custom_placeholder', 'Paste your SVG code here...\n\nExample:\n<svg viewBox="0 0 24 24" ...>...</svg>');
        customTextarea.setAttribute('aria-label', t('icon_picker_custom_tab', 'Custom SVG'));
        customTextarea.rows = 5;
        if (resolved.type === 'custom' && resolved.svg) {
            customTextarea.value = resolved.svg;
        }

        const customPreviewContainer = document.createElement('div');
        customPreviewContainer.className = 'icon-picker-custom-preview-container';

        const customPreviewLabel = document.createElement('span');
        customPreviewLabel.className = 'icon-picker-custom-preview-label';
        customPreviewLabel.textContent = t('icon_picker_preview_label', 'Preview:');

        const customPreview = document.createElement('div');
        customPreview.className = 'icon-picker-custom-preview';
        if (resolved.type === 'custom' && isValidSvg(resolved.svg)) {
            setIconContainerMarkup(customPreview, resolved.svg);
        } else {
            customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
        }

        customPreviewContainer.appendChild(customPreviewLabel);
        customPreviewContainer.appendChild(customPreview);

        // Clear button for custom mode
        const customActions = document.createElement('div');
        customActions.className = 'icon-picker-custom-actions';

        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.className = 'icon-picker-clear-btn';
        clearBtn.textContent = t('icon_picker_clear', 'Clear');
        clearBtn.addEventListener('click', () => {
            customTextarea.value = '';
            customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
            clearPresetSelection();
            updateValue('');
        });

        customActions.appendChild(clearBtn);
        customControls.appendChild(customTextarea);
        customControls.appendChild(customActions);
        customInputWrapper.appendChild(customControls);
        customInputWrapper.appendChild(customPreviewContainer);
        customPanel.appendChild(customInputWrapper);

        // Image panel
        const imagePanel = document.createElement('div');
        imagePanel.className = 'icon-picker-panel icon-picker-image-panel' + (activeTab === 'image' ? ' active' : '');
        imagePanel.setAttribute('data-panel', 'image');

        const imageWrapper = document.createElement('div');
        imageWrapper.className = 'icon-picker-image-wrapper';

        // Upload feedback and actions remain in the same column so the image
        // preview can occupy a stable, dedicated area beside them.
        const imageControls = document.createElement('div');
        imageControls.className = 'icon-picker-control-stack icon-picker-image-controls';

        const imageToolbar = document.createElement('div');
        imageToolbar.className = 'icon-picker-image-toolbar';

        const imageInput = document.createElement('input');
        imageInput.type = 'file';
        imageInput.accept = 'image/png,image/jpeg,image/webp,image/gif,image/svg+xml';
        imageInput.className = 'icon-picker-image-input';

        const imageUploadBtn = document.createElement('button');
        imageUploadBtn.type = 'button';
        imageUploadBtn.className = 'icon-picker-upload-btn';
        imageUploadBtn.textContent = t('icon_picker_upload', 'Upload image');

        const imageHint = document.createElement('p');
        imageHint.className = 'icon-picker-image-hint';
        imageHint.textContent = t('icon_picker_image_hint', 'Images are resized for small, crisp model icons.');

        const imageStatus = document.createElement('div');
        imageStatus.className = 'icon-picker-image-status';
        imageStatus.setAttribute('role', 'status');
        imageStatus.setAttribute('aria-live', 'polite');

        const imagePreviewContainer = document.createElement('div');
        imagePreviewContainer.className = 'icon-picker-custom-preview-container';

        const imagePreviewLabel = document.createElement('span');
        imagePreviewLabel.className = 'icon-picker-custom-preview-label';
        imagePreviewLabel.textContent = t('icon_picker_preview_label', 'Preview:');

        const imagePreview = document.createElement('div');
        imagePreview.className = 'icon-picker-custom-preview icon-picker-image-preview';
        if (resolved.type === 'image' && resolved.src) {
            setImagePreviewMarkup(imagePreview, resolved.src, t('icon_picker_uploaded_image', 'Uploaded icon image'));
        } else {
            imagePreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
        }

        // Connect each visible tab to its panel so the shared picker remains
        // understandable when it is embedded without an external field
        // renderer (for example in a compact workspace form).
        [
            [presetPanel, presetTab],
            [customPanel, customTab],
            [imagePanel, imageTab],
        ].forEach(([panel, tab]) => {
            const tabName = tab.getAttribute('data-tab');
            panel.id = `${pickerInstanceId}-panel-${tabName}`;
            panel.setAttribute('role', 'tabpanel');
            panel.setAttribute('aria-labelledby', tab.id);
            panel.tabIndex = -1;
            panel.hidden = tabName !== activeTab;
            panel.setAttribute('aria-hidden', String(tabName !== activeTab));
        });

        imagePreviewContainer.appendChild(imagePreviewLabel);
        imagePreviewContainer.appendChild(imagePreview);

        imageToolbar.appendChild(imageUploadBtn);
        imageToolbar.appendChild(imageInput);

        const imageActions = document.createElement('div');
        imageActions.className = 'icon-picker-custom-actions';

        const clearImageBtn = document.createElement('button');
        clearImageBtn.type = 'button';
        clearImageBtn.className = 'icon-picker-clear-btn';
        clearImageBtn.textContent = t('icon_picker_clear', 'Clear');
        clearImageBtn.addEventListener('click', () => {
            imageInput.value = '';
            imageStatus.textContent = '';
            imagePreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
            updateValue('');
            clearPresetSelection();
        });

        imageActions.appendChild(clearImageBtn);
        imageControls.appendChild(imageToolbar);
        imageControls.appendChild(imageHint);
        imageControls.appendChild(imageStatus);
        imageControls.appendChild(imageActions);
        imageWrapper.appendChild(imageControls);
        imageWrapper.appendChild(imagePreviewContainer);
        imagePanel.appendChild(imageWrapper);

        contentContainer.appendChild(presetPanel);
        if (allowCustomSvg) contentContainer.appendChild(customPanel);
        if (allowImage) contentContainer.appendChild(imagePanel);
        container.appendChild(contentContainer);

        // Current selection indicator
        const selectionIndicator = document.createElement('div');
        selectionIndicator.className = 'icon-picker-selection';

        const selectionLabel = document.createElement('span');
        selectionLabel.className = 'icon-picker-selection-label';
        selectionLabel.textContent = t('icon_picker_selected_label', 'Selected:');

        const selectionPreview = document.createElement('div');
        selectionPreview.className = 'icon-picker-selection-preview';
        updateSelectionPreview();

        selectionIndicator.appendChild(selectionLabel);
        selectionIndicator.appendChild(selectionPreview);
        container.appendChild(selectionIndicator);

        // Tab switching
        const switchTab = (tab) => {
            if (!allowedTabs.includes(tab)) return;
            activeTab = tab;
            presetTab.classList.toggle('active', activeTab === 'preset');
            customTab.classList.toggle('active', activeTab === 'custom');
            imageTab.classList.toggle('active', activeTab === 'image');
            presetPanel.classList.toggle('active', activeTab === 'preset');
            customPanel.classList.toggle('active', activeTab === 'custom');
            imagePanel.classList.toggle('active', activeTab === 'image');
            [
                [presetPanel, 'preset'],
                [customPanel, 'custom'],
                [imagePanel, 'image'],
            ].forEach(([panel, panelTab]) => {
                const isActive = panelTab === activeTab;
                panel.hidden = !isActive;
                panel.setAttribute('aria-hidden', String(!isActive));
            });
            tabs.forEach((tabButton) => {
                const isActive = tabButton.getAttribute('data-tab') === activeTab;
                tabButton.setAttribute('aria-selected', String(isActive));
                tabButton.tabIndex = isActive ? 0 : -1;
            });
        };

        presetTab.addEventListener('click', () => switchTab('preset'));
        customTab.addEventListener('click', () => switchTab('custom'));
        imageTab.addEventListener('click', () => switchTab('image'));

        // Arrow keys mirror native tab controls and avoid repeated Tab presses.
        tabsContainer.addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            const currentIndex = tabs.indexOf(document.activeElement);
            if (currentIndex < 0) return;
            event.preventDefault();
            let nextIndex = event.key === 'Home' ? 0 : tabs.length - 1;
            if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
            if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
            const nextTab = tabs[nextIndex];
            switchTab(nextTab.getAttribute('data-tab'));
            nextTab.focus();
        });

        const clearPresetSelection = () => {
            iconGrid.querySelectorAll('.icon-picker-icon-btn').forEach(btn => {
                btn.classList.remove('selected');
                btn.setAttribute('aria-pressed', 'false');
            });
        };

        // Select preset icon
        function selectPresetIcon(key) {
            // Update UI
            iconGrid.querySelectorAll('.icon-picker-icon-btn').forEach(btn => {
                const isSelected = btn.getAttribute('data-icon-key') === key;
                btn.classList.toggle('selected', isSelected);
                btn.setAttribute('aria-pressed', String(isSelected));
            });
            imageStatus.textContent = '';
            updateValue(key);
        }

        // Custom textarea input
        customTextarea.addEventListener('input', () => {
            const val = customTextarea.value.trim();
            if (isValidSvg(val)) {
                setIconContainerMarkup(customPreview, sanitizeSvgMarkup(val));
            } else if (!val) {
                customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
            } else {
                customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_invalid_svg', 'Invalid SVG')}</span>`;
            }
            clearPresetSelection();
            imageStatus.textContent = '';
            updateValue(val);
        });

        imageUploadBtn.addEventListener('click', () => {
            imageInput.click();
        });

        imageInput.addEventListener('change', async () => {
            const [file] = Array.from(imageInput.files || []);
            if (!file) {
                return;
            }
            imageStatus.textContent = t('icon_picker_processing_image', 'Preparing image...');
            imageStatus.dataset.state = 'loading';
            try {
                const imageValue = await compressIconImage(file);
                clearPresetSelection();
                customTextarea.value = '';
                customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
                setImagePreviewMarkup(imagePreview, imageValue, t('icon_picker_uploaded_image', 'Uploaded icon image'));
                imageStatus.textContent = t('icon_picker_image_ready', 'Image ready');
                imageStatus.dataset.state = 'ready';
                switchTab('image');
                updateValue(imageValue);
            } catch (error) {
                imageInput.value = '';
                imagePreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
                imageStatus.textContent = error?.message || t('icon_picker_image_failed', 'Failed to prepare image');
                imageStatus.dataset.state = 'error';
            }
        });

        // Update value and trigger callback
        function updateValue(newValue) {
            currentValue = normalizeAllowedValue(newValue);
            updateSelectionPreview();
            if (typeof onChange === 'function') {
                onChange(currentValue);
            }
        }

        // Update selection preview
        function updateSelectionPreview() {
            const resolved = resolveIconValue(currentValue);
            const iconsMap = getIconsMap();

            if (resolved.type === 'empty') {
                selectionPreview.innerHTML = `<span class="icon-picker-no-selection">${t('icon_picker_none_selected', 'None selected')}</span>`;
            } else if (resolved.type === 'preset' && iconsMap[resolved.key]) {
                selectionPreview.innerHTML = `
                    <span class="icon-picker-selection-icon">${ensureUniqueSvgIds(getPresetIconMarkup(resolved.key, presetType, iconsMap), `selection-${resolved.key}`)}</span>
                    <span class="icon-picker-selection-name">${formatIconLabel(resolved.key)}</span>
                `;
            } else if (resolved.type === 'custom' && isValidSvg(resolved.svg)) {
                selectionPreview.innerHTML = `
                    <span class="icon-picker-selection-icon">${sanitizeSvgMarkup(resolved.svg)}</span>
                    <span class="icon-picker-selection-name">${t('icon_picker_custom_svg', 'Custom SVG')}</span>
                `;
            } else if (resolved.type === 'image' && resolved.src) {
                selectionPreview.textContent = '';
                const iconSpan = document.createElement('span');
                iconSpan.className = 'icon-picker-selection-icon';
                setImagePreviewMarkup(iconSpan, resolved.src, t('icon_picker_uploaded_image', 'Uploaded icon image'));
                const nameSpan = document.createElement('span');
                nameSpan.className = 'icon-picker-selection-name';
                nameSpan.textContent = t('icon_picker_uploaded_image', 'Uploaded icon image');
                selectionPreview.appendChild(iconSpan);
                selectionPreview.appendChild(nameSpan);
            } else {
                selectionPreview.innerHTML = `<span class="icon-picker-no-selection">${t('icon_picker_invalid_icon', 'Invalid icon')}</span>`;
            }
        }

        // Public API
        const getValue = () => currentValue;
        
        const setValue = (newValue) => {
            currentValue = normalizeAllowedValue(newValue);
            const resolved = resolveIconValue(currentValue);
            
            // Update UI based on new value
            if (resolved.type === 'preset') {
                switchTab('preset');
                iconGrid.querySelectorAll('.icon-picker-icon-btn').forEach(btn => {
                    const isSelected = btn.getAttribute('data-icon-key') === resolved.key;
                    btn.classList.toggle('selected', isSelected);
                    btn.setAttribute('aria-pressed', String(isSelected));
                });
                customTextarea.value = '';
                customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
                imageInput.value = '';
                imageStatus.textContent = '';
                imagePreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
            } else if (resolved.type === 'custom') {
                switchTab('custom');
                customTextarea.value = resolved.svg || '';
                if (isValidSvg(resolved.svg)) {
                    setIconContainerMarkup(customPreview, sanitizeSvgMarkup(resolved.svg));
                } else {
                    customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_invalid_svg', 'Invalid SVG')}</span>`;
                }
                clearPresetSelection();
                imageInput.value = '';
                imageStatus.textContent = '';
                imagePreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
            } else if (resolved.type === 'image') {
                switchTab('image');
                clearPresetSelection();
                customTextarea.value = '';
                customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
                imageStatus.textContent = '';
                setImagePreviewMarkup(imagePreview, resolved.src, t('icon_picker_uploaded_image', 'Uploaded icon image'));
            } else {
                // Empty
                clearPresetSelection();
                customTextarea.value = '';
                customPreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
                imageInput.value = '';
                imageStatus.textContent = '';
                imagePreview.innerHTML = `<span class="icon-picker-no-preview">${t('icon_picker_no_preview', 'No preview')}</span>`;
            }
            
            updateSelectionPreview();
        };

        return {
            container,
            getValue,
            setValue,
        };
    };

    /**
     * Create icon picker control for schema field rendering
     * Used to replace the default input for model_icon fields
     * @param {Object} field - Schema field definition
     * @param {string} value - Current value
     * @param {Function} onChange - Optional callback when the selection changes
     * @returns {{row: HTMLElement, control: {value: string, getValue: Function}}}
     */
    const createIconPickerControl = (field, value, onChange) => {
        const translateFieldText = (i18nKey, fallback) => {
            if (i18nKey && typeof window.getTranslation === 'function') {
                return window.getTranslation(i18nKey, fallback);
            }
            return fallback;
        };

        const titleFallback = field.label || field.title || t('icon_picker_field_title', 'Model Icon');
        const descriptionFallback = field.description || t('icon_picker_field_desc', 'Select a preset icon, upload an image, or provide a custom SVG');

        // Create the row layout
        const row = document.createElement('div');
        row.className = 'settings-row column';

        const left = document.createElement('div');
        left.className = 'settings-row-left';

        const title = document.createElement('div');
        title.className = 'settings-row-title';
        title.textContent = translateFieldText(field.i18n_label, titleFallback);

        const desc = document.createElement('div');
        desc.className = 'settings-row-desc';
        desc.textContent = translateFieldText(field.i18n_description, descriptionFallback);

        left.appendChild(title);
        left.appendChild(desc);
        row.appendChild(left);

        // Create the control wrapper
        const controlWrapper = document.createElement('div');
        controlWrapper.className = 'settings-row-control icon-picker-control-wrapper';

        // Create the icon picker
        let currentValue = value || '';
        const presetType = field.iconPresetType || 'model';
        const picker = createIconPicker({
            value: currentValue,
            presetType,
            onChange: (newValue) => {
                currentValue = newValue;
                if (typeof onChange === 'function') {
                    onChange(newValue);
                }
            },
        });

        controlWrapper.appendChild(picker.container);
        row.appendChild(controlWrapper);

        // Create a virtual control object that mimics input behavior
        const control = {
            get value() {
                return picker.getValue();
            },
            set value(val) {
                picker.setValue(val);
            },
            getValue: () => picker.getValue(),
            setValue: (val) => picker.setValue(val),
        };

        return { row, control };
    };

    /**
     * Check if a field should use the icon picker
     * @param {Object} field - Schema field definition
     * @returns {boolean}
     */
    const shouldUseIconPicker = (field) => {
        if (!field || !field.key) return false;
        const key = field.key.toLowerCase();
        return key === 'model_icon' || key.endsWith('.model_icon');
    };

    // Export
    window.IconPicker = {
        createIconPicker,
        createIconPickerControl,
        shouldUseIconPicker,
        getAvailableIcons,
        resolveIconValue,
        isImageIconValue,
        isValidSvg,
        formatIconLabel,
        renderIconMarkup,
        renderModelIconMarkup,
        sanitizeIconValue,
        calculateSquareCrop,
    };
})();
