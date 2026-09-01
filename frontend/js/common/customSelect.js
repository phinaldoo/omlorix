// Central custom select adapter.
// Statically rendered `.custom-select` blocks delegate their visual and
// keyboard experience to the shared admin single-select widget.
(() => {
    'use strict';

    let selectIdCounter = 0;

    function ensureElementId(element, prefix) {
        if (!element) {
            return '';
        }
        if (!element.id) {
            selectIdCounter += 1;
            element.id = `${prefix}-${selectIdCounter}`;
        }
        return element.id;
    }

    function escapeCssAttributeValue(value) {
        const normalizedValue = String(value ?? '');
        if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
            return CSS.escape(normalizedValue);
        }
        return normalizedValue.replace(/[^a-zA-Z0-9_-]/g, (character) => `\\${character}`);
    }

    function assignStateFieldValue(field, value) {
        if (!field || typeof window === 'undefined' || !window.state) {
            return;
        }

        const { userData, serverData } = window.state;
        if (userData && Object.prototype.hasOwnProperty.call(userData, field)) {
            userData[field] = value;
            return;
        }
        if (serverData && Object.prototype.hasOwnProperty.call(serverData, field)) {
            serverData[field] = value;
        }
    }

    function getSelectParts(root) {
        return {
            trigger: root.querySelector('.select-trigger'),
            options: root.querySelector('.select-options'),
            optionElements: Array.from(root.querySelectorAll('.select-option')),
        };
    }

    function copyI18nAttributes(source, target) {
        if (!source || !target) {
            return;
        }
        ['data-i18n', 'data-i18n-attr', 'data-i18n-key'].forEach((name) => {
            if (source.hasAttribute(name)) {
                target.setAttribute(name, source.getAttribute(name));
            }
        });
    }

    function createNativeSelect(root, sourceTrigger, sourceOptions) {
        const field = sourceTrigger?.dataset?.field || root.dataset.field || root.id || '';
        const nativeSelect = document.createElement('select');
        nativeSelect.id = `${ensureElementId(root, 'custom-select')}-native`;
        nativeSelect.dataset.field = field;
        nativeSelect.dataset.customSelectNative = 'true';
        // The shared admin widget refreshes its accessible name from the
        // native select. Preserve the source trigger's naming metadata there
        // as the stable source of truth across syncs, translations, and
        // remounts instead of relying on a one-time copy to the button.
        ['aria-label', 'aria-labelledby'].forEach((name) => {
            if (sourceTrigger?.hasAttribute(name)) {
                nativeSelect.setAttribute(name, sourceTrigger.getAttribute(name));
            }
        });
        copyI18nAttributes(sourceTrigger, nativeSelect);
        // The source markup carries required state on its visible trigger.
        // Preserve it on the generated form control so validation semantics
        // survive enhancement and the shared combobox can expose the state.
        nativeSelect.required =
            sourceTrigger?.getAttribute('aria-required') === 'true' ||
            root.getAttribute('aria-required') === 'true';

        const selectedOption =
            sourceOptions.find((option) => option.classList.contains('selected')) || sourceOptions[0];

        sourceOptions.forEach((sourceOption) => {
            const option = document.createElement('option');
            option.value = String(sourceOption.dataset.value ?? '');
            option.textContent = sourceOption.textContent.trim();
            option.disabled = sourceOption.getAttribute('aria-disabled') === 'true';
            option.selected = sourceOption === selectedOption;
            option.dataset.sourceStyle = sourceOption.getAttribute('style') || '';
            copyI18nAttributes(sourceOption, option);
            nativeSelect.appendChild(option);
        });

        return nativeSelect;
    }

    function getSelectedValueFromNative(nativeSelect) {
        return nativeSelect?.value ?? '';
    }

    function emitSelectChange(state, value) {
        const event = new CustomEvent('customSelectChange', {
            detail: {
                field: state.field,
                value,
                option: state.nativeSelect.selectedOptions?.[0] || null,
                nativeSelect: state.nativeSelect,
            },
        });
        state.root.dispatchEvent(event);
    }

    function copyTriggerAccessibility(state, sourceTrigger) {
        const trigger = state.trigger;
        if (!trigger || !sourceTrigger) {
            return;
        }

        trigger.dataset.field = state.field;
        // Preserve semantic state as well as the trigger's accessible name.
        // Some forms (including meeting governance) use the generated combobox
        // for required fields, so dropping aria-required would hide that
        // requirement from assistive technology after enhancement.
        ['aria-label', 'aria-labelledby', 'aria-describedby', 'aria-required'].forEach((name) => {
            if (sourceTrigger.hasAttribute(name)) {
                trigger.setAttribute(name, sourceTrigger.getAttribute(name));
            }
        });
        copyI18nAttributes(sourceTrigger, trigger);

        if (!trigger.hasAttribute('aria-label') && !trigger.hasAttribute('aria-labelledby')) {
            const label = state.root
                .closest('.form-group, .upload-group, .toggle-group, .step-form, .warning-card, .us-setting-item, .workspace-control-field')
                ?.querySelector('.form-label, .us-setting-info h3, label, [data-i18n$="_label"], [data-i18n$="_title"]');
            if (label) {
                trigger.setAttribute('aria-labelledby', ensureElementId(label, 'custom-select-label'));
            }
        }
    }

    function copyOptionPresentation(state) {
        state.optionStyles.forEach((style, value) => {
            if (!style) {
                return;
            }
            const escapedValue = escapeCssAttributeValue(value);
            const optionText = state.menu?.querySelector(
                `.admin-select-option[data-value="${escapedValue}"] .admin-select-option-text`
            );
            if (optionText) {
                optionText.style.cssText = style;
            }
        });
    }

    function mountAdminSelect(state, sourceTrigger = null) {
        if (typeof window.initializeAdminSingleSelect !== 'function') {
            return false;
        }

        // A refreshed select no longer has its original source trigger in the
        // DOM. Retain the current generated trigger as the accessibility source
        // so dynamic option rebuilds do not lose labels or the data field name.
        const triggerAttributeSource = sourceTrigger || state.trigger;
        if (state.meta?.wrapper?.contains(state.nativeSelect)) {
            state.meta.wrapper.removeChild(state.nativeSelect);
        }
        state.meta?.wrapper?.remove();

        const placeholder = triggerAttributeSource?.textContent?.trim() || state.placeholder || '';
        state.meta = window.initializeAdminSingleSelect(state.nativeSelect, {
            key: state.field,
            placeholder,
        });

        if (!state.meta?.wrapper) {
            return false;
        }

        state.root.replaceChildren(state.meta.wrapper);
        state.trigger = state.meta.wrapper.querySelector('.admin-select-trigger');
        state.menu = state.meta.wrapper.querySelector('.admin-select-menu');
        copyTriggerAccessibility(state, triggerAttributeSource);
        copyOptionPresentation(state);
        state.meta.syncFromSelect?.();
        return true;
    }

    function rebuildNativeOptions(state, options, selectedValue) {
        if (!Array.isArray(options)) {
            return;
        }

        state.nativeSelect.replaceChildren();
        state.optionStyles.clear();

        options.forEach((item) => {
            const option = document.createElement('option');
            option.value = String(item.value ?? '');
            option.textContent = String(item.label ?? item.value ?? '');
            if (item.i18nKey) {
                option.dataset.i18n = item.i18nKey;
            }
            if (item.style) {
                option.dataset.sourceStyle = item.style;
                state.optionStyles.set(option.value, item.style);
            }
            state.nativeSelect.appendChild(option);
        });

        if (selectedValue !== undefined) {
            state.nativeSelect.value = String(selectedValue ?? '');
        }
    }

    function syncFromNative(state, { emitChange = true } = {}) {
        const value = getSelectedValueFromNative(state.nativeSelect);
        assignStateFieldValue(state.field, value);
        state.meta?.syncFromSelect?.();

        if (emitChange) {
            emitSelectChange(state, value);
        }
        if (typeof window.updateValidation === 'function') {
            window.updateValidation();
        }
    }

    function initializeAdminBackedSelect(root) {
        if (!root || root.dataset.customSelectReady === 'true') {
            return;
        }

        const parts = getSelectParts(root);
        if (!parts.trigger || !parts.options || parts.optionElements.length === 0) {
            return;
        }

        const nativeSelect = createNativeSelect(root, parts.trigger, parts.optionElements);
        const state = {
            root,
            nativeSelect,
            field: nativeSelect.dataset.field || '',
            placeholder: parts.trigger.textContent.trim(),
            optionStyles: new Map(),
            meta: null,
            trigger: null,
            menu: null,
        };

        parts.optionElements.forEach((option) => {
            state.optionStyles.set(String(option.dataset.value ?? ''), option.getAttribute('style') || '');
        });

        if (!mountAdminSelect(state, parts.trigger)) {
            return;
        }

        nativeSelect.addEventListener('change', () => syncFromNative(state));
        root.__customSelectState = state;
        root.dataset.customSelectReady = 'true';
        syncFromNative(state, { emitChange: false });
    }

    function initializeCustomSelects() {
        document.querySelectorAll('.custom-select').forEach(initializeAdminBackedSelect);
    }

    function findStateByField(field) {
        return Array.from(document.querySelectorAll('.custom-select'))
            .map((root) => root.__customSelectState)
            .find((state) => state?.field === field) || null;
    }

    function setCustomSelectValue(field, value) {
        const state = findStateByField(field);
        if (!state) {
            return;
        }

        const nextValue = String(value ?? '');
        const hasOption = Array.from(state.nativeSelect.options)
            .some((option) => String(option.value) === nextValue);
        if (!hasOption) {
            return;
        }

        state.nativeSelect.value = nextValue;
        syncFromNative(state, { emitChange: false });
    }

    function getCustomSelectValue(selectOrField) {
        const state = typeof selectOrField === 'string'
            ? findStateByField(selectOrField) || document.getElementById(selectOrField)?.__customSelectState
            : selectOrField?.__customSelectState;
        return getSelectedValueFromNative(state?.nativeSelect);
    }

    function refreshCustomSelect(root, config = {}) {
        const state = root?.__customSelectState;
        if (!state) {
            initializeAdminBackedSelect(root);
            // Apply the requested dynamic options after the first mount as
            // well. This matters when a feature refreshes a select before the
            // page-wide custom-select initializer has run.
            return root?.__customSelectState
                ? refreshCustomSelect(root, config)
                : false;
        }

        rebuildNativeOptions(state, config.options, config.value);
        if (config.value !== undefined) {
            state.nativeSelect.value = String(config.value ?? '');
        }

        mountAdminSelect(state);
        syncFromNative(state, { emitChange: false });
        return true;
    }

    refreshCustomSelect.supportsConfig = true;

    document.addEventListener('i18n:updated', () => {
        document.querySelectorAll('.custom-select').forEach((root) => {
            const state = root.__customSelectState;
            if (state) {
                state.meta?.syncFromSelect?.();
            }
        });
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeCustomSelects);
    } else {
        initializeCustomSelects();
    }

    window.initializeCustomSelects = initializeCustomSelects;
    window.refreshCustomSelect = refreshCustomSelect;
    window.setCustomSelectValue = setCustomSelectValue;
    window.getCustomSelectValue = getCustomSelectValue;
})();
