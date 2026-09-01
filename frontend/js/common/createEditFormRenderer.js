/**
 * Shared rendering primitives for project-style create and edit pages.
 *
 * Feature modules provide stable IDs, translation keys, and feature-specific
 * field markup. This renderer owns the repeated page shell, translated labels,
 * icon picker, standard fields, descriptions, and action rows so future forms
 * can reuse the same accessible structure without copying large HTML blocks.
 */
(function initializeCreateEditFormRenderer(global) {
    'use strict';

    /** Escape text before inserting it into generated HTML. */
    function escapeHtml(value = '') {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /**
     * Render a trusted attribute map while escaping every configured value.
     * Boolean true emits a valueless attribute and false/null omit it.
     */
    function renderAttributes(attributes = {}) {
        return Object.entries(attributes)
            .filter(([, value]) => value !== false && value !== null && value !== undefined)
            .map(([name, value]) => value === true
                ? ` ${name}`
                : ` ${name}="${escapeHtml(value)}"`)
            .join('');
    }

    /** Render a shared icon, or a declarative placeholder if the registry loads later. */
    function renderIcon(name, attributes = {}) {
        if (global.Icons?.withSvgAttributes) {
            return global.Icons.withSvgAttributes(name, attributes);
        }
        return `<span data-omlorix-icon="${escapeHtml(name)}"${renderAttributes(attributes)}></span>`;
    }

    /** Render an element whose visible text is managed by the i18n runtime. */
    function renderTranslatedElement({
        tag = 'span',
        key,
        fallback,
        className,
        id,
        attributes = {},
    }) {
        // Static create/edit pages are mounted before the initial i18n pass, but
        // several consumers (Agents, MCP, and managed connections) render their
        // forms after that pass has already completed. Resolve the current
        // dictionary value immediately for those late-rendered surfaces while
        // retaining data-i18n so a later language change can update the node.
        const translatedText = typeof global.getTranslation === 'function'
            ? global.getTranslation(key, fallback)
            : fallback;
        const elementAttributes = {
            ...(className ? { class: className } : {}),
            ...(id ? { id } : {}),
            ...attributes,
            'data-i18n': key,
        };
        return `<${tag}${renderAttributes(elementAttributes)}>${escapeHtml(translatedText)}</${tag}>`;
    }

    /**
     * Render the shared SVG/color picker used by Projects and Automations.
     * IDs remain feature-owned so existing behavior modules can
     * bind to the generated controls without feature-specific renderer code.
     */
    function renderIconPicker({
        idPrefix,
        triggerTranslationKey,
        triggerFallback,
        typeTranslationKey,
        typeFallback,
        pickerId,
        svgPanelId,
        previewHtml,
    }) {
        const triggerI18n = `data-tooltip:${triggerTranslationKey};aria-label:${triggerTranslationKey}`;
        const resolvedSvgPanelId = svgPanelId || `${idPrefix}IconSvgPanel`;
        const preview = previewHtml === undefined
            ? renderIcon('star', { id: `${idPrefix}IconPreview`, 'aria-hidden': 'true' })
            : previewHtml;
        return `
            <div class="svg-select"${pickerId ? ` id="${escapeHtml(pickerId)}"` : ''}>
                <button class="svg-select-button" id="${escapeHtml(idPrefix)}IconButton" type="button"
                    data-tooltip="${escapeHtml(triggerFallback)}" aria-label="${escapeHtml(triggerFallback)}"
                    data-i18n-attr="${escapeHtml(triggerI18n)}">
                    ${preview}
                </button>
                <div class="select-dropdown svg-select-dropdown" id="${escapeHtml(idPrefix)}IconDropdown">
                    <div class="svg-select-dropdown-panel active" id="${escapeHtml(resolvedSvgPanelId)}"
                        data-panel="svg" role="group"
                        aria-label="${escapeHtml(typeFallback)}" data-i18n-attr="aria-label:${escapeHtml(typeTranslationKey)}">
                        <div class="svg-select-dropdown-grid" id="${escapeHtml(idPrefix)}IconGrid"></div>
                    </div>
                    <div class="svg-select-dropdown-color-row" id="${escapeHtml(idPrefix)}ColorRow"></div>
                    <div class="projects-create-buttons">
                        <button class="om-button border"
                            id="${escapeHtml(idPrefix)}IconCancelBtn" type="button" data-i18n="common_cancel">Cancel</button>
                        <button class="om-button border submit"
                            id="${escapeHtml(idPrefix)}IconSaveBtn" type="button" data-i18n="common_save">Save</button>
                    </div>
                </div>
            </div>`;
    }

    /** Render a standard field wrapper with either translated or custom label markup. */
    function renderField({
        className = 'projects-create-input-group',
        attributes = {},
        label,
        labelHtml,
        contentHtml = '',
    }) {
        const renderedLabel = labelHtml || (label
            ? renderTranslatedElement({ tag: 'label', ...label })
            : '');
        return `
            <div class="${escapeHtml(className)}"${renderAttributes(attributes)}>
                ${renderedLabel}
                ${contentHtml}
            </div>`;
    }

    /** Render an escaped input, textarea, or select control from shared metadata. */
    function renderControl({
        tag = 'input',
        id,
        className = tag === 'textarea' ? 'projects-create-textarea' : 'projects-create-input',
        type = 'text',
        value = '',
        placeholder,
        placeholderKey,
        attributes = {},
        contentHtml,
    }) {
        const controlAttributes = {
            ...(id ? { id } : {}),
            ...(className ? { class: className } : {}),
            ...(tag === 'input' ? { type } : {}),
            ...(placeholder !== undefined ? { placeholder } : {}),
            ...(placeholderKey ? { 'data-i18n-attr': `placeholder:${placeholderKey}` } : {}),
            ...attributes,
        };
        if (tag === 'input') {
            if (value !== '') controlAttributes.value = value;
            return `<input${renderAttributes(controlAttributes)}>`;
        }
        const content = contentHtml === undefined ? escapeHtml(value) : contentHtml;
        return `<${tag}${renderAttributes(controlAttributes)}>${content}</${tag}>`;
    }

    /** Render translated helper or validation copy below a form control. */
    function renderFieldMessage(message, defaultClassName) {
        if (!message) return '';
        return renderTranslatedElement({
            tag: message.tag || 'p',
            className: message.className || defaultClassName,
            key: message.key,
            fallback: message.fallback,
            id: message.id,
            attributes: message.attributes || {},
        });
    }

    /** Render a conventional labelled form control with optional hint and error. */
    function renderControlField({
        className = 'projects-create-input-group',
        attributes = {},
        label,
        labelHtml,
        control,
        beforeControlHtml = '',
        afterControlHtml = '',
        hint,
        error,
    }) {
        const normalizedLabel = label && control?.id
            ? { ...label, attributes: { for: control.id, ...(label.attributes || {}) } }
            : label;
        return renderField({
            className,
            attributes,
            label: normalizedLabel,
            labelHtml,
            contentHtml: `${beforeControlHtml}${renderControl(control)}${afterControlHtml}` +
                `${renderFieldMessage(hint, 'skills-input-hint')}` +
                `${renderFieldMessage(error, 'skills-input-error')}`,
        });
    }

    /** Render the shared upload/library picker used by Automation and Agent forms. */
    function renderFilePicker({
        selectedId,
        inputId,
        uploadButtonId,
        libraryButtonId,
        dropdownId,
        uploadLabel,
        libraryLabel,
        uploadIconHtml = '',
        libraryIconHtml = '',
    }) {
        return `
            <div class="shared-files-picker">
                <div class="shared-files-selected" id="${escapeHtml(selectedId)}"></div>
                <div class="shared-files-actions">
                    <input type="file" id="${escapeHtml(inputId)}" multiple hidden>
                    <button type="button" class="shared-files-action-btn" id="${escapeHtml(uploadButtonId)}">
                        ${uploadIconHtml}
                        ${renderTranslatedElement({ tag: 'span', ...uploadLabel })}
                    </button>
                    <button type="button" class="shared-files-action-btn secondary shared-files-library-trigger"
                        id="${escapeHtml(libraryButtonId)}" aria-expanded="false">
                        ${libraryIconHtml}
                        ${renderTranslatedElement({ tag: 'span', ...libraryLabel })}
                    </button>
                </div>
                <div class="shared-file-library-dropdown" id="${escapeHtml(dropdownId)}"></div>
            </div>`;
    }

    /** Render the standard labelled checkbox/switch settings card. */
    function renderToggleCard({
        className = 'memories-card',
        rowClassName = 'memories-setting-row',
        id,
        label,
        description,
        inputAttributes = {},
        switchClassName = '',
    }) {
        const labelId = `${id}Label`;
        const descriptionId = `${id}Description`;
        const input = `<input${renderAttributes({
            id,
            type: 'checkbox',
            'aria-labelledby': labelId,
            'aria-describedby': descriptionId,
            ...inputAttributes,
        })}>`;
        const control = switchClassName
            ? `<label class="${escapeHtml(switchClassName)}">${input}<span aria-hidden="true"></span></label>`
            : input;
        return `
            <div class="${escapeHtml(className)}">
                <div class="${escapeHtml(rowClassName)}">
                    <span>
                        ${renderTranslatedElement({ tag: 'label', id: labelId, attributes: { for: id }, ...label })}
                        ${renderTranslatedElement({ tag: 'small', id: descriptionId, ...description })}
                    </span>
                    ${control}
                </div>
            </div>`;
    }

    /** Render the shared icon-and-name field layout. */
    function renderNameIconField({
        groupClass,
        label,
        iconPicker,
        input,
        error,
    }) {
        const inputAttributes = {
            type: 'text',
            id: input.id,
            class: input.className,
            placeholder: input.placeholder,
            'data-i18n-attr': `placeholder:${input.placeholderKey}`,
            'aria-describedby': error.id,
            'aria-invalid': 'false',
        };
        const errorAttributes = {
            'aria-hidden': 'true',
            ...(error.hidden ? { hidden: true } : {}),
        };
        return renderField({
            className: groupClass,
            label: { ...label, attributes: { for: input.id } },
            contentHtml: `
                <div class="projects-name-and-icon-row">
                    ${renderIconPicker(iconPicker)}
                    <div class="projects-name-input-field">
                        <input${renderAttributes(inputAttributes)}>
                        ${renderTranslatedElement({
                            tag: 'p',
                            key: error.key,
                            fallback: error.fallback,
                            className: error.className,
                            id: error.id,
                            attributes: errorAttributes,
                        })}
                    </div>
                </div>`,
        });
    }

    /** Render a reusable translated description card. */
    function renderDescription({ className, titleClass, title, textClass, paragraphs }) {
        return `
            <div class="${escapeHtml(className)}">
                ${renderTranslatedElement({ tag: 'p', className: titleClass, ...title })}
                ${paragraphs.map((paragraph) => renderTranslatedElement({
                    tag: 'p',
                    className: textClass,
                    ...paragraph,
                })).join('')}
            </div>`;
    }

    /** Render the standard bottom action row for a create or edit form. */
    function renderActions({ className, buttons }) {
        const markup = buttons.map((button) => renderTranslatedElement({
            tag: 'button',
            key: button.key,
            fallback: button.fallback,
            className: button.className,
            id: button.id,
            attributes: {
                type: button.type || 'button',
                ...(button.hidden ? { hidden: true } : {}),
                ...(button.attributes || {}),
            },
        })).join('');
        return `<div class="${escapeHtml(className)}">${markup}</div>`;
    }

    /** Render a shared page header with optional subtitle or feature actions. */
    function renderHeader({
        className = 'projects-header',
        title,
        titleId,
        titleAttributes = {},
        subtitle,
        subtitleClassName = 'projects-header-subtitle',
        contentHtml = '',
        actionsHtml = '',
    }) {
        // Custom header content owns its full copy and needs no artificial key.
        const titleMarkup = contentHtml ? '' : renderTranslatedElement({
            tag: 'p',
            className: `${className.split(' ')[0]}-title`,
            id: titleId,
            attributes: titleAttributes,
            ...title,
        });
        const subtitleMarkup = !contentHtml && subtitle
            ? renderTranslatedElement({ tag: 'p', className: subtitleClassName, ...subtitle })
            : '';
        const copy = contentHtml || (subtitleMarkup
            ? `<div>${titleMarkup}${subtitleMarkup}</div>`
            : titleMarkup);
        return `<div class="${escapeHtml(className)}">${copy}${actionsHtml}</div>`;
    }

    /**
     * Render the shared single-select shell used for models, Skills, and any
     * future icon-backed form choice. Feature modules still own their option
     * data and selection side effects.
     */
    function renderSingleSelect({
        kind,
        triggerId,
        dropdownId,
        iconHtml = '',
        iconStyle,
        label,
        placeholder = false,
        caretHtml = '',
        search,
        listId,
        bodyHtml = '',
    }) {
        const baseClass = `shared-${escapeHtml(kind)}-select`;
        const listboxId = listId || dropdownId;
        const searchMarkup = search ? `
            <div class="shared-model-select-search">
                <input type="text" class="shared-model-select-search-input" id="${escapeHtml(search.id)}"
                    placeholder="${escapeHtml(search.placeholder)}" autocomplete="off" spellcheck="false"
                    value="${escapeHtml(search.value || '')}">
            </div>` : '';
        const dropdownBody = listId
            ? `<div class="${baseClass}-list" id="${escapeHtml(listId)}" role="listbox"
                aria-labelledby="${escapeHtml(triggerId)}"></div>`
            : bodyHtml;
        return `
            <button type="button" class="${baseClass}-trigger" id="${escapeHtml(triggerId)}"
                aria-haspopup="listbox" aria-controls="${escapeHtml(listboxId)}" aria-expanded="false">
                <span class="${baseClass}-icon"${iconStyle ? ` style="${escapeHtml(iconStyle)}"` : ''}>${iconHtml}</span>
                <span class="${baseClass}-label${placeholder ? ' placeholder' : ''}">${escapeHtml(label)}</span>
                <span class="${baseClass}-caret" aria-hidden="true">${caretHtml}</span>
            </button>
            <div class="${baseClass}-dropdown" id="${escapeHtml(dropdownId)}"${listId
                ? ''
                : ` role="listbox" aria-labelledby="${escapeHtml(triggerId)}"`}>
                ${searchMarkup}
                ${dropdownBody}
            </div>`;
    }

    /** Bind the repeated open/close/search behavior for a shared select shell. */
    function bindSingleSelect({
        container,
        triggerId,
        dropdownId,
        searchId,
        onOpen,
        onClose,
        onSearch,
    }) {
        const trigger = container?.querySelector(`#${triggerId}`);
        const dropdown = container?.querySelector(`#${dropdownId}`);
        const searchInput = searchId ? container?.querySelector(`#${searchId}`) : null;

        /** Return the current options after a feature has rendered or filtered them. */
        const getOptions = () => Array.from(dropdown?.querySelectorAll('[role="option"]:not([disabled])') || []);

        /** Focus the selected option when possible, otherwise use the requested edge. */
        const focusOption = (preference = 'selected') => {
            const options = getOptions();
            if (!options.length) return;
            const selected = options.find((option) => option.getAttribute('aria-selected') === 'true');
            const target = preference === 'last'
                ? options[options.length - 1]
                : preference === 'first'
                    ? options[0]
                    : (selected || options[0]);
            options.forEach((option) => { option.tabIndex = option === target ? 0 : -1; });
            target.focus();
        };

        /** Defer focus until feature onOpen callbacks have finished rendering options. */
        const deferOptionFocus = (preference) => {
            const schedule = typeof global.requestAnimationFrame === 'function'
                ? global.requestAnimationFrame.bind(global)
                : typeof global.setTimeout === 'function'
                    ? (callback) => global.setTimeout(callback, 0)
                    : (callback) => callback();
            schedule(() => focusOption(preference));
        };

        const setOpen = (open, { optionFocus, restoreFocus = false } = {}) => {
            trigger?.classList.toggle('open', open);
            dropdown?.classList.toggle('open', open);
            trigger?.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) {
                onOpen?.({ trigger, dropdown, searchInput });
                if (optionFocus) deferOptionFocus(optionFocus);
            } else {
                onClose?.({ trigger, dropdown, searchInput });
                if (restoreFocus) trigger?.focus();
            }
        };

        trigger?.addEventListener('click', (event) => {
            event.stopPropagation();
            const open = !trigger.classList.contains('open');
            // Native button activation produces detail=0 for keyboard input.
            // Skill selects have no search field, so move keyboard users into
            // the option list as soon as the popup opens.
            setOpen(open, {
                optionFocus: open && !searchInput && event.detail === 0 ? 'selected' : null,
            });
        });
        trigger?.addEventListener('keydown', (event) => {
            if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
            event.preventDefault();
            event.stopPropagation();
            setOpen(true, { optionFocus: event.key === 'ArrowUp' ? 'last' : 'selected' });
        });
        searchInput?.addEventListener('input', (event) => onSearch?.(event.target.value, event));
        dropdown?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                setOpen(false, { restoreFocus: true });
                return;
            }

            const options = getOptions();
            if (!options.length) return;
            const currentIndex = options.indexOf(event.target);
            if (['Enter', ' '].includes(event.key) && currentIndex >= 0) {
                event.preventDefault();
                event.target.click();
                return;
            }
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            if (
                currentIndex < 0
                && (event.target !== searchInput || !['ArrowDown', 'ArrowUp'].includes(event.key))
            ) {
                return;
            }

            // Arrow keys from the model search field enter the result list.
            // Once an option owns focus, use conventional roving navigation.
            event.preventDefault();
            let nextIndex;
            if (event.key === 'Home') nextIndex = 0;
            else if (event.key === 'End') nextIndex = options.length - 1;
            else if (currentIndex < 0) nextIndex = event.key === 'ArrowUp' ? options.length - 1 : 0;
            else nextIndex = (currentIndex + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
            options.forEach((option, index) => { option.tabIndex = index === nextIndex ? 0 : -1; });
            options[nextIndex].focus();
        });
        return { trigger, dropdown, searchInput, setOpen, focusOption };
    }

    /** Render one hidden project-style page ready for its behavior module. */
    function renderPage({
        id,
        contentClass = 'projects-content',
        pageHidden = true,
        pageAttributes = {},
        headerClass = 'projects-header',
        title,
        titleId,
        titleAttributes,
        subtitle,
        headerContentHtml,
        headerActionsHtml,
        formTag = 'div',
        formClass = 'projects-create-form',
        formId,
        formAttributes = {},
        bodyHtml = '',
        actions,
    }) {
        const pageAttrs = {
            ...(id ? { id } : {}),
            ...(pageHidden ? { style: 'display: none;' } : {}),
            ...pageAttributes,
        };
        const formAttrs = {
            ...(formId ? { id: formId } : {}),
            class: formClass,
            ...formAttributes,
        };
        return `
            <div class="${escapeHtml(contentClass)}"${renderAttributes(pageAttrs)}>
                ${renderHeader({
                    className: headerClass,
                    title,
                    titleId,
                    titleAttributes,
                    subtitle,
                    contentHtml: headerContentHtml,
                    actionsHtml: headerActionsHtml,
                })}
                <${formTag}${renderAttributes(formAttrs)}>
                    ${bodyHtml}
                    ${actions ? renderActions(actions) : ''}
                </${formTag}>
            </div>`;
    }

    /**
     * Mount a group of pages exactly once beneath a feature container.
     * Throwing on missing containers or duplicate IDs makes script-order and
     * configuration mistakes visible during development instead of leaving a
     * partially initialized form.
     */
    function mountPages({ containerId, pages }) {
        const container = global.document?.getElementById(containerId);
        if (!container) {
            throw new Error(`Cannot mount create/edit pages: #${containerId} was not found`);
        }

        const pageIds = new Set();
        pages.forEach((page) => {
            if (pageIds.has(page.id)) {
                throw new Error(`Cannot mount create/edit pages: duplicate page id "${page.id}"`);
            }
            pageIds.add(page.id);
        });

        const pagesToMount = pages.filter((page) => !global.document.getElementById(page.id));
        if (!pagesToMount.length) return;
        container.insertAdjacentHTML('beforeend', pagesToMount.map(renderPage).join(''));
    }

    global.CreateEditFormRenderer = Object.freeze({
        escapeHtml,
        renderActions,
        renderAttributes,
        renderDescription,
        renderControl,
        renderControlField,
        renderField,
        renderFieldMessage,
        renderFilePicker,
        renderHeader,
        renderIcon,
        renderSingleSelect,
        bindSingleSelect,
        renderToggleCard,
        renderIconPicker,
        renderNameIconField,
        renderPage,
        renderTranslatedElement,
        mountPages,
    });
})(window);
