/**
 * Shared Field Validation Module
 * Provides consistent validation UX across all admin forms.
 */
const FieldValidation = (function () {
    /**
     * Check if a field value is empty (for required field validation).
     */
    const isFieldValueEmpty = (field, control) => {
        if (!control) return true;
        const fieldType = (field?.type || '').toLowerCase();
        switch (fieldType) {
            case 'boolean':
            case 'toggle':
                return false; // Booleans are never empty
            case 'select':
                return !control.value;
            case 'number':
                return control.value === '';
            case 'string_list':
                // For keyword tags UI
                if (control.dataset?.keywordTags !== undefined) {
                    try {
                        const keywords = JSON.parse(control.dataset.keywordTags || '[]');
                        return !keywords.length;
                    } catch (error) {
                        return true;
                    }
                }
                return control.value?.trim() === '';
            default:
                if (control.type === 'checkbox') {
                    return false; // Checkboxes are never empty
                }
                return control.value?.trim?.() === '' || control.value === '';
        }
    };

    /**
     * Add error state to a field row.
     */
    const setFieldError = (row, message = 'This field is required') => {
        if (!row) return;
        row.classList.add('has-error');
        const control = row.querySelector('input, select, textarea');
        if (control) {
            // The shared .field-error style gives the invalid control a clear
            // visual border, while aria-invalid exposes the same state to
            // assistive technologies.
            control.classList.add('field-error');
            control.setAttribute('aria-invalid', 'true');
        }
        // Add error message if not already present
        let errorEl = row.querySelector('.field-error-message');
        if (!errorEl) {
            errorEl = document.createElement('p');
            errorEl.className = 'field-error-message';
            errorEl.setAttribute('role', 'alert');
            const controlWrapper = row.querySelector('.settings-row-control');
            if (controlWrapper) {
                controlWrapper.appendChild(errorEl);
            } else {
                row.appendChild(errorEl);
            }
        }
        errorEl.textContent = message;
        // Trigger shake animation
        row.classList.remove('shake-error');
        void row.offsetWidth; // Force reflow
        row.classList.add('shake-error');
    };

    /**
     * Clear error state from a field row.
     */
    const clearFieldError = (row) => {
        if (!row) return;
        row.classList.remove('has-error', 'shake-error');
        const control = row.querySelector('input, select, textarea');
        if (control) {
            control.classList.remove('field-error');
            control.removeAttribute('aria-invalid');
        }
        const errorEl = row.querySelector('.field-error-message');
        if (errorEl) {
            errorEl.remove();
        }
    };

    /**
     * Validate numeric constraints against min/max attributes.
     */
    const validateNumberConstraints = (field, control, row) => {
        if (!control) {
            return true;
        }
        const rawValue = control.value;
        if (rawValue === '' || rawValue === null || rawValue === undefined) {
            return true;
        }

        const label = field?.label || field?.key || control.name || 'This field';
        const numericValue = Number(rawValue);
        if (Number.isNaN(numericValue)) {
            setFieldError(row, `${label} must be a valid number`);
            return false;
        }

        const attributes = field?.attributes || {};
        const parseConstraint = (value) => {
            if (value === null || value === undefined || value === '') {
                return null;
            }
            const parsed = Number(value);
            return Number.isNaN(parsed) ? null : parsed;
        };
        const minConstraint = parseConstraint(attributes.min ?? control.min);
        const maxConstraint = parseConstraint(attributes.max ?? control.max);

        if (minConstraint !== null && numericValue < minConstraint) {
            setFieldError(row, `${label} must be at least ${minConstraint}`);
            return false;
        }
        if (maxConstraint !== null && numericValue > maxConstraint) {
            setFieldError(row, `${label} must be at most ${maxConstraint}`);
            return false;
        }
        return true;
    };

    /**
     * Clear all field errors in a container.
     */
    const clearAllFieldErrors = (container) => {
        if (!container) return;
        const errorRows = container.querySelectorAll('.settings-row.has-error');
        errorRows.forEach(clearFieldError);
    };

    /**
     * Validate required fields in a controls collection.
     * @param {Map|Array} controls - Map of { field, control } or array of { field, control }
     * @param {Object} options - Options for validation
     * @returns {Array} Array of invalid field rows
     */
    const validateRequiredFields = (controls, options = {}) => {
        const invalidRows = [];
        const entries = controls instanceof Map ? Array.from(controls.values()) : controls;

        entries.forEach((entry) => {
            // Support both { field, control } and { control } with field info on control
            const field = entry.field || {};
            const control = entry.control || entry;
            if (!control) return;

            const row = control.closest?.('.settings-row');
            if (!row) return;

            // Skip hidden fields (dependency not satisfied or hidden)
            if (row.hidden || row.style.display === 'none') return;

            // Clear any previous error
            clearFieldError(row);

            // Check if field is required and empty
            const isRequired = field.required || control.required || control.hasAttribute?.('required');
            const isEmpty = isFieldValueEmpty(field, control);
            let hasError = false;
            if (isRequired && isEmpty) {
                const label = field.label || field.key || control.name || 'This field';
                setFieldError(row, helperFormatT('validation_field_required', '{field} is required.', { field: label }));
                hasError = true;
            } else if (!isEmpty) {
                const fieldType = (field?.type || control.type || '').toLowerCase();
                if (fieldType === 'number') {
                    if (!validateNumberConstraints(field, control, row)) {
                        hasError = true;
                    }
                }
            }

            if (hasError) {
                invalidRows.push(row);
            }
        });

        return invalidRows;
    };

    /**
     * Scroll to the first invalid field with smooth animation.
     */
    const scrollToFirstInvalidField = (invalidRows) => {
        if (!invalidRows?.length) return;
        const firstRow = invalidRows[0];
        firstRow.scrollIntoView({
            behavior: 'smooth',
            block: 'center',
        });
        // Focus the input if possible
        const control = firstRow.querySelector('input, select, textarea');
        if (control) {
            setTimeout(() => control.focus(), 300);
        }
    };

    /**
     * Attach input listeners to clear errors on user input.
     * @param {Map|Array} controls - Map or array of { field, control }
     */
    const attachErrorClearListeners = (controls) => {
        const entries = controls instanceof Map ? Array.from(controls.values()) : controls;
        entries.forEach((entry) => {
            const control = entry.control || entry;
            if (!control) return;
            const row = control.closest?.('.settings-row');
            if (!row) return;
            const clearOnInput = () => {
                if (row.classList.contains('has-error')) {
                    clearFieldError(row);
                }
            };
            // Prevent duplicate listeners
            if (control.dataset?.errorClearBound === 'true') return;
            control.addEventListener('input', clearOnInput);
            control.addEventListener('change', clearOnInput);
            control.dataset.errorClearBound = 'true';
        });
    };

    /**
     * Run validation and show errors. Returns true if valid.
     * @param {Map|Array} controls - Map or array of { field, control }
     * @param {Object} options - Options
     * @returns {boolean} True if all required fields are valid
     */
    const validate = (controls, options = {}) => {
        const invalidRows = validateRequiredFields(controls, options);
        if (invalidRows.length > 0) {
            const fieldCount = invalidRows.length;
            const message = options.errorMessage ||
                `Please fill in ${fieldCount} required field${fieldCount > 1 ? 's' : ''}.`;
            if (options.notify !== false && typeof notifyError === 'function') {
                notifyError(message);
            }
            scrollToFirstInvalidField(invalidRows);
            return false;
        }
        return true;
    };

    return {
        isFieldValueEmpty,
        setFieldError,
        clearFieldError,
        clearAllFieldErrors,
        validateRequiredFields,
        scrollToFirstInvalidField,
        attachErrorClearListeners,
        validate,
    };
})();

window.FieldValidation = FieldValidation;
