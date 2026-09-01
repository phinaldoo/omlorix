/**
 * Shared UI helpers for the admin media generation pages
 * (image, audio, video, music). Builds settings-section /
 * settings-row markup that matches the rest of the admin UI.
 */
(function () {
    const t = (key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key);

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    /**
     * Build a single settings-row.
     * @param {object} opts
     *   - title: string
     *   - description?: string
     *   - control: HTMLElement
     *   - column?: boolean (stack title above control)
     *   - rightExtraClass?: string
     */
    function buildSettingsRow({ title, description, control, column, rightExtraClass }) {
        const row = document.createElement('div');
        row.className = 'settings-row';
        if (column) row.classList.add('column');

        const left = document.createElement('div');
        left.className = 'settings-row-left';
        const titleEl = document.createElement('p');
        titleEl.className = 'settings-row-title';
        titleEl.textContent = title || '';
        left.appendChild(titleEl);
        if (description) {
            const descEl = document.createElement('p');
            descEl.className = 'settings-row-desc';
            descEl.textContent = description;
            left.appendChild(descEl);
        }
        row.appendChild(left);

        const right = document.createElement('div');
        right.className = 'settings-row-right';
        if (rightExtraClass) right.classList.add(rightExtraClass);
        if (control) right.appendChild(control);
        row.appendChild(right);

        return row;
    }

    /**
     * Build a settings-section with optional header and a body.
     * @param {object} opts
     *   - title?: string
     *   - description?: string
     *   - extraClass?: string
     *   - bodyClass?: string
     */
    function buildSettingsSection({ title, description, extraClass, bodyClass }) {
        const section = document.createElement('section');
        section.className = 'settings-section';
        if (extraClass) section.classList.add(extraClass);

        if (title || description) {
            const header = document.createElement('div');
            header.className = 'settings-section-header';
            const inner = document.createElement('div');
            if (title) {
                const titleEl = document.createElement('h3');
                titleEl.className = 'settings-section-title';
                titleEl.textContent = title;
                inner.appendChild(titleEl);
            }
            if (description) {
                const descEl = document.createElement('p');
                descEl.className = 'settings-section-description';
                descEl.textContent = description;
                inner.appendChild(descEl);
            }
            header.appendChild(inner);
            section.appendChild(header);
        }

        const body = document.createElement('div');
        body.className = 'settings-section-body';
        if (bodyClass) body.classList.add(bodyClass);
        section.appendChild(body);

        return { section, body };
    }

    /**
     * Build a status pill / chip.
     */
    function buildStatusPill(text, variant = 'neutral') {
        const span = document.createElement('span');
        span.className = `media-gen-pill media-gen-pill--${variant}`;
        span.textContent = text == null ? '' : String(text);
        return span;
    }

    /**
     * Build a chip group (list of label: value chips) for showing
     * the active per-model settings.
     */
    function buildChipGroup(entries) {
        const wrapper = document.createElement('div');
        wrapper.className = 'media-gen-chip-group';
        for (const { label, value } of entries) {
            if (value == null || value === '') continue;
            const chip = document.createElement('span');
            chip.className = 'media-gen-chip';
            const labelEl = document.createElement('span');
            labelEl.className = 'media-gen-chip-label';
            labelEl.textContent = label;
            const valueEl = document.createElement('span');
            valueEl.className = 'media-gen-chip-value';
            valueEl.textContent = String(value);
            chip.appendChild(labelEl);
            chip.appendChild(valueEl);
            wrapper.appendChild(chip);
        }
        return wrapper;
    }

    /**
     * Empty-state section (used when no provider/model is configured yet).
     */
    function buildEmptyStateSection({ icon, title, description }) {
        const section = document.createElement('section');
        section.className = 'settings-section media-gen-empty-state';
        const body = document.createElement('div');
        body.className = 'settings-section-body media-gen-empty-state-body';

        if (icon) {
            const iconWrap = document.createElement('div');
            iconWrap.className = 'media-gen-empty-state-icon';
            iconWrap.innerHTML = icon;
            body.appendChild(iconWrap);
        }
        if (title) {
            const titleEl = document.createElement('p');
            titleEl.className = 'media-gen-empty-state-title';
            titleEl.textContent = title;
            body.appendChild(titleEl);
        }
        if (description) {
            const descEl = document.createElement('p');
            descEl.className = 'media-gen-empty-state-desc';
            descEl.textContent = description;
            body.appendChild(descEl);
        }
        section.appendChild(body);
        return section;
    }

    /**
     * Build the same accessible loading spinner used by the rest of admin.
     * This keeps media-generation pages visually aligned with schema pages.
     */
    function buildLoadingPlaceholder(message) {
        if (typeof window.createAdminLoadingPlaceholder === 'function') {
            return window.createAdminLoadingPlaceholder({
                message: message || t('admin_loading_ellipsis', 'Loading...'),
                className: 'admin-loading-placeholder--inline media-gen-loading-placeholder',
            });
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'admin-loading-placeholder admin-loading-placeholder--inline media-gen-loading-placeholder';
        wrapper.setAttribute('role', 'status');
        wrapper.setAttribute('aria-live', 'polite');

        const box = document.createElement('div');
        box.className = 'admin-loading-box';

        const spinner = document.createElement('div');
        spinner.className = 'admin-loading-spinner';
        spinner.setAttribute('aria-hidden', 'true');

        const text = document.createElement('p');
        text.className = 'admin-loading-text';
        text.textContent = message || t('admin_loading_ellipsis', 'Loading...');

        box.appendChild(spinner);
        box.appendChild(text);
        wrapper.appendChild(box);
        return wrapper;
    }

    function showLoading(container, message) {
        clearContainer(container);
        if (container) {
            container.appendChild(buildLoadingPlaceholder(message));
        }
    }

    /**
     * Build a select element (native, styled with `.input`).
     */
    function buildSelect({ id, placeholder } = {}) {
        const select = document.createElement('select');
        select.className = 'input media-gen-select';
        if (id) select.id = id;
        if (placeholder) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = placeholder;
            select.appendChild(opt);
        }
        return select;
    }

    /**
     * Upgrade a native media-generation select to the same admin custom select
     * widgets used by schema-rendered settings. The native select remains the
     * source of truth so existing change handlers keep working.
     */
    function upgradeSelect(select, field = {}) {
        if (!select || !select.parentNode) {
            return null;
        }

        if (select.multiple) {
            return window.upgradeAdminMultiSelect?.(select, field) || null;
        }

        return window.upgradeAdminSingleSelect?.(select, field) || null;
    }

    /**
     * Build a text/number input element.
     */
    function buildInput({ id, type = 'text', placeholder, attributes } = {}) {
        const input = document.createElement('input');
        input.type = type;
        input.className = 'input media-gen-input';
        if (id) input.id = id;
        if (placeholder) input.placeholder = placeholder;
        if (attributes && typeof attributes === 'object') {
            if (attributes.min !== undefined) input.min = String(attributes.min);
            if (attributes.max !== undefined) input.max = String(attributes.max);
            if (attributes.step !== undefined) input.step = String(attributes.step);
        }
        return input;
    }

    /**
     * Build a styled toggle (boolean) control.
     */
    function buildToggle({ id } = {}) {
        const wrap = document.createElement('label');
        wrap.className = 'toggle-switch';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.className = 'toggle-input';
        if (id) input.id = id;
        const slider = document.createElement('span');
        slider.className = 'toggle-slider';
        wrap.appendChild(input);
        wrap.appendChild(slider);
        return { wrap, input };
    }

    /**
     * Build the standard "Save / busy" footer row.
     * @param {object} opts
     *   - savingLabel: text shown on the button while saving
     *   - defaultLabel: default button text
     *   - title?: optional title
     *   - description?: optional description
     */
    function buildSaveRow({
        title,
        description,
        defaultLabel,
        savingLabel,
        buttonId,
    }) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'om-button border submit';
        button.disabled = true;
        if (buttonId) button.id = buttonId;
        const span = document.createElement('span');
        span.textContent = defaultLabel;
        button.appendChild(span);

        const row = buildSettingsRow({
            title: title || t('media_gen_save_row_title', 'Save configuration'),
            description: description || t('media_gen_save_row_desc', 'Apply the selected provider, model, and parameters.'),
            control: button,
        });

        const setBusy = (busy) => {
            button.disabled = busy;
            span.textContent = busy ? savingLabel : defaultLabel;
        };
        const setEnabled = (enabled) => {
            button.disabled = !enabled;
        };

        return { row, button, setBusy, setEnabled };
    }

    /**
     * Build a compact action bar for one-step-at-a-time wizard pages.
     */
    function buildWizardActions({
        backLabel,
        primaryLabel,
        primaryId,
        primaryDisabled = false,
    } = {}) {
        const bar = document.createElement('div');
        bar.className = 'media-gen-wizard-actions';

        const backButton = document.createElement('button');
        backButton.type = 'button';
        backButton.className = 'om-button border cancel';
        const backSpan = document.createElement('span');
        backSpan.textContent = backLabel || t('btn_back', 'Back');
        backButton.appendChild(backSpan);
        bar.appendChild(backButton);

        let primaryButton = null;
        let primarySpan = null;
        if (primaryLabel) {
            primaryButton = document.createElement('button');
            primaryButton.type = 'button';
            primaryButton.className = 'om-button border submit';
            primaryButton.disabled = primaryDisabled;
            if (primaryId) primaryButton.id = primaryId;
            primarySpan = document.createElement('span');
            primarySpan.textContent = primaryLabel;
            primaryButton.appendChild(primarySpan);
            bar.appendChild(primaryButton);
        }

        const setPrimaryBusy = (busy, busyLabel, defaultLabel) => {
            if (!primaryButton || !primarySpan) return;
            primaryButton.disabled = busy;
            primarySpan.textContent = busy ? busyLabel : defaultLabel || primaryLabel;
        };

        return { bar, backButton, primaryButton, setPrimaryBusy };
    }

    function focusFirstControl(container) {
        const candidates = Array.from(container?.querySelectorAll?.(
            'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled])'
        ) || []);
        const target = candidates.find((element) => {
            if (element.classList?.contains('admin-select-native') || element.classList?.contains('admin-multiselect-native')) {
                return false;
            }
            return element.offsetParent !== null;
        });
        target?.focus?.();
    }

    function clearContainer(el) {
        if (el) el.innerHTML = '';
    }

    /**
     * Keep wizard steps out of both the visual and accessibility trees until
     * their parent selection exists. `.settings-row` uses flex display, so an
     * inline display value is required in addition to the native hidden flag.
     */
    function setStepVisible(element, visible) {
        if (!element) return;
        element.hidden = !visible;
        element.style.display = visible ? '' : 'none';
    }

    window.MediaGenerationUI = {
        t,
        escapeHtml,
        buildSettingsRow,
        buildSettingsSection,
        buildStatusPill,
        buildChipGroup,
        buildEmptyStateSection,
        buildLoadingPlaceholder,
        showLoading,
        buildSelect,
        upgradeSelect,
        buildInput,
        buildToggle,
        buildSaveRow,
        buildWizardActions,
        focusFirstControl,
        clearContainer,
        setStepVisible,
    };
})();
