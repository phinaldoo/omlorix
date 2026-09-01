(function initializeFormValidation(globalScope) {
    const DEFAULT_GROUP_SELECTOR = [
        '.projects-create-input-group',
        '.todos-list-editor-field',
        '.memories-card',
        '.cs-field',
        '.files-edit-modal-field',
        '.files-folder-modal-field',
    ].join(',');

    function getFieldGroup(inputEl, options = {}) {
        const selector = options.groupSelector || DEFAULT_GROUP_SELECTOR;
        return inputEl?.closest?.(selector) || null;
    }

    function showInputError(inputEl, errorEl, message, options = {}) {
        if (!inputEl) return false;

        inputEl.classList.add(options.inputErrorClass || 'input-error');
        inputEl.setAttribute('aria-invalid', 'true');

        const group = getFieldGroup(inputEl, options);
        if (group) group.classList.add(options.groupErrorClass || 'has-error');

        if (errorEl) {
            if (message) errorEl.textContent = message;
            if (options.errorVisibleClass !== null) {
                errorEl.classList.add(options.errorVisibleClass || 'visible');
            }
            errorEl.hidden = false;
            errorEl.removeAttribute('hidden');
            errorEl.setAttribute('aria-hidden', 'false');
            if (options.role) errorEl.setAttribute('role', options.role);
        }

        if (options.scroll !== false) {
            inputEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        if (options.focus !== false) {
            inputEl.focus();
        }
        return true;
    }

    function clearInputError(inputEl, errorEl, options = {}) {
        if (!inputEl) return false;

        inputEl.classList.remove(options.inputErrorClass || 'input-error');
        inputEl.setAttribute('aria-invalid', 'false');

        const group = getFieldGroup(inputEl, options);
        if (group) group.classList.remove(options.groupErrorClass || 'has-error');

        if (errorEl) {
            if (options.errorVisibleClass !== null) {
                errorEl.classList.remove(options.errorVisibleClass || 'visible');
            }
            if (options.hideWithHidden !== false) {
                errorEl.hidden = true;
                errorEl.setAttribute('hidden', '');
            }
            errorEl.setAttribute('aria-hidden', 'true');
        }

        return true;
    }

    globalScope.FormValidation = {
        showInputError,
        clearInputError,
    };
}(typeof window !== 'undefined' ? window : globalThis));
