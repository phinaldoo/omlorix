// Bridges setup-page native selects to the admin schema select widget.
// The visual dropdown and keyboard behavior come from admin/helper.js.
(function () {
    'use strict';

    function getStateBucket(field) {
        if (!field || !window.state) {
            return null;
        }
        if (window.state.userData && Object.prototype.hasOwnProperty.call(window.state.userData, field)) {
            return window.state.userData;
        }
        if (window.state.serverData && Object.prototype.hasOwnProperty.call(window.state.serverData, field)) {
            return window.state.serverData;
        }
        return null;
    }

    function assignSetupSelectValue(field, value) {
        const bucket = getStateBucket(field);
        if (bucket) {
            bucket[field] = value;
        }
    }

    function getSetupSelect(field) {
        if (!field) {
            return null;
        }
        const escaped = typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
            ? CSS.escape(String(field))
            : String(field).replace(/"/g, '\\"');
        return document.querySelector(`select[data-field="${escaped}"]`);
    }

    function getSetupSelectTrigger(field) {
        const select = getSetupSelect(field);
        return select?._singleSelect?.wrapper?.querySelector('.admin-select-trigger') || null;
    }

    function syncSetupSelect(select) {
        if (!select) {
            return;
        }
        select._singleSelect?.syncFromSelect?.();
    }

    function syncSetupSelects() {
        document.querySelectorAll('select[data-setup-select="admin"]').forEach(syncSetupSelect);
    }

    function setCustomSelectValue(field, value) {
        const select = getSetupSelect(field);
        if (!select) {
            return;
        }

        const nextValue = String(value ?? '');
        const hasOption = Array.from(select.options).some((option) => String(option.value) === nextValue);
        if (!hasOption) {
            return;
        }

        select.value = nextValue;
        assignSetupSelectValue(field, nextValue);
        syncSetupSelect(select);

        if (typeof window.updateValidation === 'function') {
            window.updateValidation();
        }
    }

    function initializeSetupAdminSelects(root = document) {
        if (typeof window.initializeAdminSingleSelect !== 'function') {
            return;
        }

        root.querySelectorAll('select[data-setup-select="admin"]').forEach((select) => {
            if (select.dataset.setupSelectReady === 'true') {
                syncSetupSelect(select);
                return;
            }

            const field = select.dataset.field || select.name || select.id;
            const placeholder = select.dataset.placeholder || '';
            const i18nPlaceholder = select.dataset.i18nPlaceholder || '';
            const parent = select.parentNode;
            const nextSibling = select.nextSibling;
            if (!parent) {
                return;
            }

            const meta = window.initializeAdminSingleSelect(select, {
                key: field,
                placeholder,
                i18n_placeholder: i18nPlaceholder,
            });

            if (!meta?.wrapper) {
                return;
            }

            select._singleSelect = meta;
            select.classList.add('admin-select-native');
            parent.insertBefore(meta.wrapper, nextSibling);
            const trigger = meta.wrapper.querySelector('.admin-select-trigger');
            const labelledBy = select.getAttribute('aria-labelledby');
            const describedBy = select.getAttribute('aria-describedby');
            if (trigger && labelledBy) {
                trigger.setAttribute('aria-labelledby', labelledBy);
            }
            if (trigger && describedBy) {
                trigger.setAttribute('aria-describedby', describedBy);
            }
            select.dataset.setupSelectReady = 'true';
            assignSetupSelectValue(field, select.value);

            select.addEventListener('change', () => {
                assignSetupSelectValue(field, select.value);
                if (field === 'language' && typeof window.setLanguage === 'function') {
                    window.setLanguage(select.value);
                }
                if (typeof window.updateValidation === 'function') {
                    window.updateValidation();
                }
            });
        });
    }

    document.addEventListener('i18n:updated', syncSetupSelects);

    window.initializeSetupAdminSelects = initializeSetupAdminSelects;
    window.getSetupSelectTrigger = getSetupSelectTrigger;
    window.setCustomSelectValue = setCustomSelectValue;
    window.syncSetupSelects = syncSetupSelects;
})();
