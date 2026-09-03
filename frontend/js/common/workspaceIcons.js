(function initializeWorkspaceIconUtils(globalScope) {
    let generatedPickerId = 0;

    const WORKSPACE_ICON_COLORS = Object.freeze([
        { id: 'red', name: 'Red', hex: '#E53935' },
        { id: 'orange', name: 'Orange', hex: '#FB8C00' },
        { id: 'amber', name: 'Amber', hex: '#FFB300' },
        { id: 'green', name: 'Green', hex: '#43A047' },
        { id: 'teal', name: 'Teal', hex: '#00897B' },
        { id: 'blue', name: 'Blue', hex: '#1E88E5' },
        { id: 'indigo', name: 'Indigo', hex: '#6366f1' },
        { id: 'purple', name: 'Purple', hex: '#8E24AA' },
        { id: 'pink', name: 'Pink', hex: '#D81B60' },
        { id: 'grey', name: 'Grey', hex: '#757575' },
    ]);

    function escapeHtml(value = '') {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function normalizeColor(value, fallback = '#6366f1') {
        if (typeof value !== 'string') return fallback;
        const trimmed = value.trim();
        return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(trimmed) ? trimmed : fallback;
    }

    function hexToRgba(color, alpha = 0.18) {
        const normalized = normalizeColor(color, '');
        if (!normalized) return `rgba(99, 102, 241, ${alpha})`;

        let hex = normalized.slice(1);
        if (hex.length === 3) {
            hex = hex.split('').map((char) => char + char).join('');
        }

        const red = parseInt(hex.slice(0, 2), 16);
        const green = parseInt(hex.slice(2, 4), 16);
        const blue = parseInt(hex.slice(4, 6), 16);
        return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
    }

    function isSvgMarkup(value) {
        return typeof value === 'string' && value.includes('<') && value.includes('>');
    }

    function humanizeIconId(iconId) {
        return String(iconId || '')
            .replace(/[_:-]+/g, ' ')
            .replace(/\b\w/g, (char) => char.toUpperCase());
    }

    function getWorkspaceIconOptions(iconOptions) {
        const source = iconOptions
            || globalScope.workspaceIconPickerOptions
            || globalScope.Icons?.workspaceIconPickerOptions
            || [];

        if (Array.isArray(source)) {
            return source
                .map((option, index) => {
                    if (typeof option === 'string') {
                        return {
                            id: String(index),
                            name: `Icon ${index + 1}`,
                            svg: option,
                        };
                    }
                    if (!option || typeof option !== 'object') return null;
                    const id = String(option.id || option.key || index).trim();
                    return {
                        ...option,
                        id,
                        name: option.name || humanizeIconId(id),
                        svg: option.svg || globalScope.Icons?.[option.iconKey] || '',
                    };
                })
                .filter((option) => option?.id && option?.svg);
        }

        if (source && typeof source === 'object') {
            return Object.entries(source).map(([id, svg]) => ({
                id,
                name: humanizeIconId(id),
                svg,
            }));
        }

        return [];
    }

    function getWorkspaceIconOptionById(iconId, iconOptions) {
        const normalized = String(iconId || '').trim();
        const options = getWorkspaceIconOptions(iconOptions);
        if (!normalized) return options[0] || null;
        return options.find((option) => option.id === normalized || option.iconKey === normalized) || null;
    }

    function findWorkspaceIconOptionBySvg(svgMarkup, iconOptions) {
        const normalized = String(svgMarkup || '').trim();
        if (!normalized) return null;
        return getWorkspaceIconOptions(iconOptions).find((option) => {
            const candidate = String(option.svg || '').trim();
            return candidate === normalized || candidate.includes(normalized) || normalized.includes(candidate);
        }) || null;
    }

    function resolveWorkspaceStoredIcon(value, {
        iconOptions,
        defaultIconId = 'folder',
        defaultColor = '#6366f1',
        color,
    } = {}) {
        const options = getWorkspaceIconOptions(iconOptions);
        const fallbackOption = getWorkspaceIconOptionById(defaultIconId, options) || options[0] || null;
        const fallback = {
            type: 'preset',
            iconId: fallbackOption?.id || defaultIconId,
            svg: fallbackOption?.svg || '',
            color: normalizeColor(color, normalizeColor(defaultColor, '#6366f1')),
        };
        const withColor = (resolved, storedColor) => ({
            ...resolved,
            color: normalizeColor(storedColor, fallback.color),
        });
        const presetResult = (option, storedColor) => withColor({
            type: 'preset',
            iconId: option?.id || fallback.iconId,
            svg: option?.svg || fallback.svg,
        }, storedColor);
        const resolvePresetValue = (candidate, storedColor) => {
            const option = getWorkspaceIconOptionById(candidate, options);
            return option ? presetResult(option, storedColor) : null;
        };
        if (typeof value === 'string') {
            const trimmed = value.trim();
            if (!trimmed) return fallback;

            if (trimmed.startsWith('{')) {
                try {
                    const parsed = JSON.parse(trimmed);
                    if (parsed && typeof parsed === 'object') {
                        const storedColor = parsed.color || color;
                        const preset = resolvePresetValue(parsed.preset, storedColor);
                        if (preset) return preset;
                    }
                } catch (error) {
                    console.warn('Failed to parse stored workspace icon JSON', error);
                }
                return fallback;
            }

            const preset = resolvePresetValue(trimmed, color);
            if (preset) return preset;

            if (isSvgMarkup(trimmed)) {
                const svgPreset = findWorkspaceIconOptionBySvg(trimmed, options);
                return svgPreset ? presetResult(svgPreset, color) : fallback;
            }

        }

        return fallback;
    }

    function renderWorkspaceIcon(iconData, { size = 22, defaultIconId = 'folder', iconOptions } = {}) {
        const option = getWorkspaceIconOptionById(iconData?.iconId, iconOptions)
            || getWorkspaceIconOptionById(defaultIconId, iconOptions)
            || getWorkspaceIconOptions(iconOptions)[0];
        return `<span style="width:${size}px;height:${size}px;display:inline-flex;justify-content:center;align-items:center">${option?.svg || ''}</span>`;
    }

    function createWorkspaceIconPicker({
        state,
        refs,
        iconOptions,
        colors = WORKSPACE_ICON_COLORS,
        defaultIconId = 'folder',
        defaultColor = '#6366f1',
        translate = (_key, fallback) => fallback,
        renderIcon = renderWorkspaceIcon,
        variant = 'workspace',
    } = {}) {
        const pickerState = state || {};
        const usesSharedSvgSelect = variant === 'svg-select';
        const options = getWorkspaceIconOptions(iconOptions);
        const palette = Array.isArray(colors) && colors.length ? colors : WORKSPACE_ICON_COLORS;
        const defaultOption = getWorkspaceIconOptionById(defaultIconId, options) || options[0] || null;
        const normalizedDefaultColor = normalizeColor(defaultColor, '#6366f1').toLowerCase();
        const defaultColorIndex = Math.max(0, palette.findIndex((entry) => (
            normalizeColor(entry?.hex, '').toLowerCase() === normalizedDefaultColor
        )));
        let openStateSnapshot = null;
        let dropdownController = null;

        function getRefs() {
            return typeof refs === 'function' ? refs() : (refs || {});
        }

        /**
         * Keep the trigger and popup accessibility state synchronized with the
         * visual open state. Some consumers render their picker markup
         * statically while others create it at runtime, so the shared
         * controller owns these attributes instead of requiring every feature
         * to duplicate them.
         */
        function updateAccessibility(dom = getRefs()) {
            const trigger = dom.trigger;
            const dropdown = dom.dropdown;
            if (!trigger) return;

            trigger.setAttribute('aria-haspopup', 'dialog');

            if (!dropdown) return;
            if (!dropdown.id) {
                generatedPickerId += 1;
                dropdown.id = trigger.id
                    ? `${trigger.id}Dropdown`
                    : `workspaceIconPickerDropdown${generatedPickerId}`;
            }

            trigger.setAttribute('aria-controls', dropdown.id);
            if (!dropdown.hasAttribute('role')) dropdown.setAttribute('role', 'dialog');
            if (!dropdown.hasAttribute('aria-label') && trigger.getAttribute('aria-label')) {
                dropdown.setAttribute('aria-label', trigger.getAttribute('aria-label'));
            }
            if (!usesSharedSvgSelect || !dropdownController) {
                trigger.setAttribute('aria-expanded', pickerState.isOpen ? 'true' : 'false');
                dropdown.setAttribute('aria-hidden', pickerState.isOpen ? 'false' : 'true');
            }
        }

        function selectedColor() {
            return palette[pickerState.selectedColorIndex] || palette[defaultColorIndex] || palette[0];
        }

        function selectedIcon() {
            return getWorkspaceIconOptionById(pickerState.selectedIconId, options) || defaultOption || options[0];
        }

        function reset(iconValue = '', colorValue = defaultColor) {
            const resolved = resolveWorkspaceStoredIcon(iconValue, {
                iconOptions: options,
                defaultIconId,
                defaultColor,
                color: colorValue,
            });
            const normalizedResolvedColor = normalizeColor(resolved.color, '').toLowerCase();
            const colorIndex = palette.findIndex((entry) => (
                normalizeColor(entry?.hex, '').toLowerCase() === normalizedResolvedColor
            ));
            pickerState.selectedIconId = resolved.iconId || defaultOption?.id || defaultIconId;
            pickerState.selectedColorIndex = colorIndex >= 0 ? colorIndex : defaultColorIndex;
            dropdownController?.close({ reason: 'reset' });
            pickerState.isOpen = false;
            openStateSnapshot = null;

            // Resets can happen while a form is being closed. Clear any open
            // presentation state so the next visit starts from this new value.
            const dom = getRefs();
            dom.picker?.classList.remove('open');
            dom.dropdown?.classList.remove('open');
            updateAccessibility(dom);
        }

        function currentIconData() {
            const colorEntry = selectedColor();
            const option = selectedIcon();
            return {
                type: 'preset',
                iconId: option?.id || defaultIconId,
                svg: option?.svg || '',
                color: colorEntry.hex,
            };
        }

        function serialize({ includeColor = true } = {}) {
            const data = currentIconData();
            if (!includeColor) return data.iconId;
            return JSON.stringify({ preset: data.iconId, color: data.color });
        }

        function captureState() {
            return {
                selectedIconId: pickerState.selectedIconId,
                selectedColorIndex: pickerState.selectedColorIndex,
            };
        }

        function restoreOpenState() {
            if (!openStateSnapshot) return;
            Object.assign(pickerState, openStateSnapshot);
            render();
            updatePreview();
        }

        function render() {
            const dom = getRefs();
            dom.picker?.querySelectorAll('.todos-icon-picker-caret').forEach((caret) => {
                if (!caret.innerHTML.trim()) caret.innerHTML = globalScope.Icons?.chevron || '';
            });
            if (dom.svgGrid) {
                dom.svgGrid.innerHTML = options.map((option, index) => {
                    // A stable translated label plus an option number avoids
                    // generating translation keys from icon IDs at runtime.
                    const label = `${translate('common_icon_choose', 'Choose icon')} ${index + 1}`;
                    const selected = option.id === pickerState.selectedIconId;
                    const optionClass = usesSharedSvgSelect ? 'svg-select-dropdown-grid-svg-item' : 'todos-icon-option';
                    return `
                        <button type="button" class="${optionClass} ${selected ? 'selected' : ''}"
                                data-icon-id="${escapeHtml(option.id)}" title="${escapeHtml(label)}"
                                aria-label="${escapeHtml(label)}" aria-pressed="${selected ? 'true' : 'false'}">
                            ${option.svg || ''}
                        </button>
                    `;
                }).join('');
            }
            if (dom.colorGrid) {
                dom.colorGrid.innerHTML = palette.map((colorEntry, index) => {
                    const label = `${translate('todos_icon_picker_colors', 'Colors')} ${index + 1}`;
                    const colorClass = usesSharedSvgSelect ? 'svg-select-dropdown-color-item' : 'todos-color-option';
                    return `
                        <button type="button" class="${colorClass} ${index === pickerState.selectedColorIndex ? 'selected' : ''}"
                                data-color-index="${index}" title="${escapeHtml(label)}"
                                aria-label="${escapeHtml(label)}" aria-pressed="${index === pickerState.selectedColorIndex ? 'true' : 'false'}"
                                style="background-color: ${escapeHtml(colorEntry.hex)}">
                        </button>
                    `;
                }).join('');
            }
            updateAccessibility(dom);
        }

        function updatePreview() {
            const dom = getRefs();
            const iconData = currentIconData();
            if (!dom.preview) return;
            if (usesSharedSvgSelect) {
                // The compact trigger matches project and automation icon buttons.
                dom.preview.style.backgroundColor = '';
                dom.preview.style.color = iconData.color;
            } else {
                dom.preview.style.backgroundColor = iconData.color;
            }
            dom.preview.innerHTML = renderIcon(iconData, { size: 24, defaultIconId, iconOptions: options });
        }

        function updateDropdownPlacement() {
            const dom = getRefs();
            const picker = dom.picker;
            const trigger = dom.trigger;
            const dropdown = dom.dropdown || picker?.querySelector(
                usesSharedSvgSelect ? '.svg-select-dropdown' : '.todos-icon-picker-dropdown'
            );
            if (!picker || !trigger || !dropdown || typeof window === 'undefined') return;

            if (usesSharedSvgSelect) {
                globalScope.positionDropdownAtTrigger?.(trigger, dropdown, {
                    align: 'start',
                    viewportMargin: 12,
                });
                return;
            }

            const triggerRect = trigger.getBoundingClientRect();
            const dropdownRect = dropdown.getBoundingClientRect();
            const spacing = 8;
            const viewportMargin = 12;
            const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
            const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
            const roomBelow = viewportHeight - triggerRect.bottom - spacing - viewportMargin;
            const roomAbove = triggerRect.top - spacing - viewportMargin;
            const openAbove = roomBelow < dropdownRect.height && roomAbove > roomBelow;

            picker.classList.toggle('open-up', openAbove);
            picker.classList.toggle('align-end', triggerRect.left + dropdownRect.width > viewportWidth - viewportMargin);
        }

        function setOpen(open) {
            if (usesSharedSvgSelect) {
                const controller = ensureDropdownController();
                const shouldOpen = typeof open === 'boolean' ? open : !controller?.isOpen();
                controller?.[shouldOpen ? 'open' : 'close']({ reason: 'api' });
                return;
            }

            const dom = getRefs();
            pickerState.isOpen = typeof open === 'boolean' ? open : !pickerState.isOpen;
            if (pickerState.isOpen) {
                updateDropdownPlacement();
            }
            dom.picker?.classList.toggle('open', pickerState.isOpen);
            updateAccessibility(dom);
        }

        function closeSharedPicker({ save = false, restoreFocus = false } = {}) {
            if (!usesSharedSvgSelect) {
                setOpen(false);
                if (restoreFocus) getRefs().trigger?.focus?.();
                return;
            }
            ensureDropdownController()?.close({
                reason: save ? 'save' : 'cancel',
                restoreFocus,
            });
        }

        function ensureDropdownController() {
            if (!usesSharedSvgSelect || dropdownController) return dropdownController;
            const dom = getRefs();
            if (!dom.trigger || !dom.dropdown || typeof globalScope.createDropdownController !== 'function') {
                return null;
            }
            updateAccessibility(dom);
            dropdownController = globalScope.createDropdownController({
                id: `${dom.dropdown.id}-controller`,
                trigger: dom.trigger,
                dropdown: dom.dropdown,
                root: dom.picker,
                group: 'svg-select-dropdown',
                onBeforeOpen: () => {
                    openStateSnapshot = captureState();
                    updateDropdownPlacement();
                },
                onOpen: () => {
                    pickerState.isOpen = true;
                },
                onBeforeClose: ({ reason }) => {
                    if (reason !== 'save' && reason !== 'reset') restoreOpenState();
                },
                onClose: () => {
                    pickerState.isOpen = false;
                    openStateSnapshot = null;
                },
                shouldCloseOnOutsideInteraction: (event) => {
                    const eventPath = typeof event.composedPath === 'function' ? event.composedPath() : [];
                    return !eventPath.includes(getRefs().picker);
                },
            });
            return dropdownController;
        }

        function selectPreset(iconId) {
            pickerState.selectedIconId = getWorkspaceIconOptionById(iconId, options)?.id || defaultOption?.id || defaultIconId;
            render();
            updatePreview();
        }

        function selectColor(index) {
            pickerState.selectedColorIndex = Math.max(0, Math.min(Number(index) || 0, palette.length - 1));
            render();
            updatePreview();
        }

        function bind() {
            const dom = getRefs();
            if (!dom.picker || dom.picker.dataset.workspaceIconPickerBound === 'true') return;
            dom.picker.dataset.workspaceIconPickerBound = 'true';
            updateAccessibility(dom);
            if (usesSharedSvgSelect) {
                ensureDropdownController();
                const repositionIfOpen = () => {
                    if (pickerState.isOpen) updateDropdownPlacement();
                };
                globalScope.addEventListener?.('resize', repositionIfOpen);
                globalScope.addEventListener?.('scroll', repositionIfOpen, true);
            } else dom.trigger?.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                setOpen();
            });
            dom.svgGrid?.addEventListener('click', (event) => {
                const option = event.target.closest('[data-icon-id]');
                if (!option) return;
                selectPreset(option.dataset.iconId);
            });
            dom.colorGrid?.addEventListener('click', (event) => {
                const option = event.target.closest('[data-color-index]');
                if (!option) return;
                selectColor(parseInt(option.dataset.colorIndex, 10));
            });
            dom.saveButton?.addEventListener('click', (event) => {
                event.stopPropagation();
                closeSharedPicker({ save: true, restoreFocus: true });
            });
            dom.cancelButton?.addEventListener('click', (event) => {
                event.stopPropagation();
                closeSharedPicker({ restoreFocus: true });
            });
            if (!usesSharedSvgSelect) dom.picker.addEventListener('keydown', (event) => {
                if (event.key !== 'Escape' || !pickerState.isOpen) return;
                event.preventDefault();
                event.stopPropagation();
                setOpen(false);
                dom.trigger?.focus?.();
            });
            if (!usesSharedSvgSelect) document.addEventListener('click', (event) => {
                const current = getRefs().picker;
                // Rendering a selection replaces the grid's innerHTML while
                // this click is still bubbling. The original event target is
                // therefore detached before it reaches document, making
                // contains(event.target) incorrectly report an outside click.
                // The composed path is captured when dispatch begins and
                // continues to identify clicks that originated in the picker.
                const eventPath = typeof event.composedPath === 'function' ? event.composedPath() : [];
                const originatedInsidePicker = eventPath.includes(current) || current?.contains(event.target);
                if (current && pickerState.isOpen && !originatedInsidePicker) {
                    setOpen(false);
                }
            });
        }

        reset(defaultOption?.id || defaultIconId, defaultColor);

        return {
            state: pickerState,
            getIconData: currentIconData,
            reset,
            render,
            updatePreview,
            setOpen,
            selectPreset,
            selectColor,
            serialize,
            bind,
            close: closeSharedPicker,
        };
    }

    globalScope.WorkspaceIconUtils = {
        WORKSPACE_ICON_COLORS,
        escapeHtml,
        normalizeColor,
        hexToRgba,
        isSvgMarkup,
        getWorkspaceIconOptions,
        getWorkspaceIconOptionById,
        findWorkspaceIconOptionBySvg,
        resolveWorkspaceStoredIcon,
        renderWorkspaceIcon,
        createWorkspaceIconPicker,
    };
}(typeof window !== 'undefined' ? window : globalThis));
