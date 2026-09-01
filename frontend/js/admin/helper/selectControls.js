/**
 * Return the non-secret marker used to preserve an existing masked setting.
 *
 * The actual secret never reaches the browser. Returning null for new or empty
 * fields keeps their normal translated placeholder and submission behavior.
 */
function getMaskedFieldSubmissionMarker(field) {
    const isMaskedSecret =
        (field?.type === 'string' || field?.type === 'textarea')
        && String(field?.input_type || '').toLowerCase() === 'password'
        && field?.redact_value === true
        && field?.masked_placeholder === true
        && field?.masked_value_set === true;
    if (!isMaskedSecret || typeof field?.placeholder !== 'string' || !field.placeholder) {
        return null;
    }
    return field.placeholder;
}

function isTimezoneSelectField(field) {
    const tokens = [
        field?.key,
        field?.id,
        field?.name,
        field?.label,
    ]
        .map((value) => String(value || '').trim().toLowerCase())
        .filter(Boolean);
    return tokens.some((value) => value === 'timezone' || value.includes('timezone') || value.includes('time_zone'));
}

function configureAdminSelectSearchInput(searchInput, searchConfig = {}) {
    const usesDefaultPlaceholder = !searchConfig.placeholder;
    const placeholder =
        searchConfig.placeholder || helperT('admin_search_placeholder', 'Search...');

    searchInput.placeholder = placeholder;
    searchInput.setAttribute('aria-label', placeholder);
    if (usesDefaultPlaceholder) {
        searchInput.setAttribute(
            'data-i18n-attr',
            'placeholder:admin_search_placeholder;aria-label:admin_search_placeholder'
        );
    }
}

function initializeAdminSingleSelect(select, field) {
    const wrapper = document.createElement('div');
    wrapper.className = 'admin-select';

    let typeBuffer = '';
    let lastTypeTs = 0;
    let escapeReg = null;
    const searchConfig = field?.search || {};
    const isSearchable = Boolean(field?.searchable || searchConfig.enabled || isTimezoneSelectField(field));
    const noOptionsMessage =
        searchConfig.emptyMessage ||
        helperT('admin_no_options_available', 'No options available');
    const noSearchResultsMessage = searchConfig.noResultsMessage || noOptionsMessage;
    const searchAutoFocusEnabled = searchConfig.autoFocus !== false;
    const disableMobileSearchAutoFocus = Boolean(searchConfig.disableMobileAutoFocus);

    const isLikelyMobileViewport = () => {
        if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
            return false;
        }
        return (
            window.matchMedia('(max-width: 768px)').matches ||
            window.matchMedia('(hover: none) and (pointer: coarse)').matches
        );
    };

    const triggerId = `${select.id || `${field.key || 'single'}-select`}-trigger`;
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.id = triggerId;
    trigger.className = 'admin-select-trigger placeholder';
    // This is a select-only combobox: the button exposes the current value and
    // opens the associated listbox. The role makes aria-required valid and the
    // explicit relationship keeps the popup discoverable to assistive tech.
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    /*
     * The native select becomes aria-hidden after enhancement, so its explicit
     * accessible name must move to the generated trigger. Schema-rendered
     * selects are usually labelled by their field wrapper, while standalone
     * toolbar selects commonly provide aria-label directly.
     */
    const syncAccessibleName = () => {
        const labelledBy = select.getAttribute('aria-labelledby');
        const ariaLabel = select.getAttribute('aria-label');
        trigger.removeAttribute('aria-labelledby');
        trigger.removeAttribute('aria-label');
        if (labelledBy) {
            trigger.setAttribute('aria-labelledby', labelledBy);
        } else if (ariaLabel) {
            trigger.setAttribute('aria-label', ariaLabel);
        }
    };
    syncAccessibleName();
    if (select.required) {
        trigger.setAttribute('aria-required', 'true');
    }

    const triggerLabel = document.createElement('span');
    triggerLabel.className = 'admin-select-value';
    trigger.appendChild(triggerLabel);

    const triggerCaret = document.createElement('span');
    triggerCaret.className = 'admin-select-caret';
    triggerCaret.innerHTML = getAdminIconMarkup('chevron');
    trigger.appendChild(triggerCaret);

    const menu = document.createElement('div');
    menu.id = `${triggerId}-listbox`;
    menu.className = 'admin-select-menu';
    menu.setAttribute('role', 'listbox');
    trigger.setAttribute('aria-controls', menu.id);

    wrapper.append(trigger, menu);

    let searchInput = null;
    let searchContainer = null;
    if (isSearchable) {
        searchContainer = document.createElement('label');
        searchContainer.className = 'admin-select-search';

        const searchIcon = document.createElement('span');
        searchIcon.className = 'admin-select-search-icon';
        searchIcon.innerHTML = getAdminIconMarkup('magnifyingGlass');
        searchContainer.appendChild(searchIcon);

        searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.className = 'admin-select-search-input';
        configureAdminSelectSearchInput(searchInput, searchConfig);
        searchInput.autocomplete = 'off';
        searchInput.spellcheck = false;
        searchContainer.appendChild(searchInput);

        menu.appendChild(searchContainer);
    }

    const getFocusableOptions = () =>
        Array.from(menu.querySelectorAll('.admin-select-option:not(:disabled)')).filter(
            (option) => !option.hidden
        );

    const clearKeyboardFocus = () => {
        menu.querySelectorAll('.admin-select-option.keyboard-focus').forEach((option) => {
            option.classList.remove('keyboard-focus');
        });
    };

    const focusOption = (option) => {
        if (!option) {
            return;
        }
        clearKeyboardFocus();
        option.classList.add('keyboard-focus');
        option.focus();
        option.scrollIntoView({ block: 'nearest' });
    };

    const clearTypeAhead = () => {
        typeBuffer = '';
        lastTypeTs = 0;
        clearKeyboardFocus();
    };

    const setSearchFocusRingVisible = (visible) => {
        if (!searchContainer) {
            return;
        }
        searchContainer.classList.toggle('admin-select-search-show-focus', Boolean(visible));
    };

    const handleTypeAhead = (event) => {
        if (!wrapper.classList.contains('open')) {
            return false;
        }

        if (event.key === 'Enter') {
            const focused =
                menu.querySelector('.admin-select-option.keyboard-focus') || document.activeElement;
            if (focused && focused.classList.contains('admin-select-option')) {
                event.preventDefault();
                focused.click();
                return true;
            }
            return false;
        }

        if (event.key === 'Escape') {
            event.preventDefault();
            if (typeof event.stopImmediatePropagation === 'function') {
                event.stopImmediatePropagation();
            }
            event.stopPropagation();
            closeMenu();
            trigger.focus();
            return true;
        }

        if (event.key.length !== 1 || event.metaKey || event.ctrlKey || event.altKey) {
            return false;
        }

        const now = Date.now();
        if (now - lastTypeTs > 700) {
            typeBuffer = '';
        }
        lastTypeTs = now;

        typeBuffer += event.key.toLowerCase();

        const options = getFocusableOptions();
        if (!options.length) {
            return false;
        }

        const keyboardTarget =
            menu.querySelector('.admin-select-option.keyboard-focus') || document.activeElement;
        const startIndex = options.indexOf(keyboardTarget);
        const ordered =
            startIndex >= 0
                ? [...options.slice(startIndex + 1), ...options.slice(0, startIndex + 1)]
                : options;

        let match = ordered.find((opt) =>
            (opt.textContent || '').trim().toLowerCase().startsWith(typeBuffer)
        );

        if (!match && typeBuffer.length > 1) {
            typeBuffer = event.key.toLowerCase();
            match = ordered.find((opt) =>
                (opt.textContent || '').trim().toLowerCase().startsWith(typeBuffer)
            );
        }

        if (match) {
            event.preventDefault();
            focusOption(match);
            return true;
        }

        return false;
    };

    const optionButtons = new Map();
    const placeholder = getFieldPlaceholder(field, helperT('admin_select_placeholder_single', 'Select an option...'));
    const emptyValueIsOption = Boolean(field?.emptyValueIsOption);
    const empty = document.createElement('div');
    empty.className = 'admin-select-empty';

    const updateEmptyState = () => {
        if (!optionButtons.size) {
            empty.textContent = noOptionsMessage;
            if (!empty.parentNode) {
                menu.appendChild(empty);
            }
            return;
        }

        if (searchInput && searchInput.value.trim() && !getFocusableOptions().length) {
            empty.textContent = noSearchResultsMessage;
            if (!empty.parentNode) {
                menu.appendChild(empty);
            }
            return;
        }

        empty.remove();
    };

    const filterOptions = () => {
        const searchTerm = (searchInput?.value || '').trim().toLowerCase();
        optionButtons.forEach((button) => {
            const matches =
                !searchTerm || (button.dataset.searchText || '').includes(searchTerm);
            button.hidden = !matches;
        });
        updateEmptyState();
    };

    const rebuildOptionButtons = () => {
        optionButtons.clear();
        menu.querySelectorAll('.admin-select-option').forEach((option) => option.remove());
        empty.remove();

        Array.from(select.options).forEach((opt, optionIndex) => {
            const value = String(opt.value ?? '');
            const optionButton = document.createElement('button');
            optionButton.type = 'button';
            optionButton.id = `${triggerId}-option-${optionIndex + 1}`;
            optionButton.className = 'admin-select-option';
            optionButton.dataset.value = value;
            optionButton.dataset.searchText = String(opt.textContent || value || '—').trim().toLowerCase();
            optionButton.setAttribute('role', 'option');
            optionButton.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
            optionButton.disabled = opt.disabled;

            const dot = document.createElement('span');
            dot.className = 'admin-select-option-dot';
            optionButton.appendChild(dot);

            const text = document.createElement('span');
            text.className = 'admin-select-option-text';
            text.textContent = opt.textContent || value || '—';
            optionButton.appendChild(text);

            if (opt.selected) {
                optionButton.classList.add('selected');
            }

            optionButton.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (opt.disabled) {
                    return;
                }
                select.value = value;
                select.dispatchEvent(new Event('change', { bubbles: true }));
                closeMenu();
            });

            optionButtons.set(value, optionButton);
            menu.appendChild(optionButton);
        });

        filterOptions();
    };

    select.classList.add('admin-select-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    wrapper.appendChild(select);

    const updateSummary = () => {
        const selectedOption = select.selectedOptions?.[0];
        if (!selectedOption || (selectedOption.value === '' && !emptyValueIsOption)) {
            triggerLabel.textContent = placeholder;
            trigger.classList.add('placeholder');
            return;
        }
        trigger.classList.remove('placeholder');
        triggerLabel.textContent = selectedOption.textContent || selectedOption.value;
    };

    const updateOptionButton = (value, selected) => {
        const button = optionButtons.get(value);
        if (!button) {
            return;
        }
        button.classList.toggle('selected', selected);
        button.setAttribute('aria-selected', selected ? 'true' : 'false');
    };

    const syncActiveDescendant = () => {
        const selectedButton = optionButtons.get(String(select.value ?? ''));
        if (!selectedButton?.id) {
            trigger.removeAttribute('aria-activedescendant');
            return;
        }
        // Chrome derives a select-only combobox's exposed value from its
        // committed option. Keep that relationship explicit so accessibility
        // caches cannot retain the previous visible value after a save/render.
        trigger.setAttribute('aria-activedescendant', selectedButton.id);
    };

    const syncFromSelect = () => {
        syncAccessibleName();
        optionButtons.forEach((button, value) => {
            const nativeOption = Array.from(select.options).find(
                (option) => String(option.value ?? '') === value
            );
            const textNode = button.querySelector('.admin-select-option-text');
            if (nativeOption && textNode) {
                textNode.textContent = nativeOption.textContent || value || '—';
                button.dataset.searchText = String(nativeOption.textContent || value || '—').trim().toLowerCase();
            }
            updateOptionButton(value, select.value === value);
        });
        updateSummary();
        syncActiveDescendant();
        filterOptions();
    };

    select.addEventListener('change', syncFromSelect);

    const closeMenu = () => {
        if (!wrapper.classList.contains('open')) {
            return;
        }
        wrapper.classList.remove('open');
        menu.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('click', handleDocumentClick, true);
        document.removeEventListener('keydown', handleKeydown, true);
        if (escapeReg) {
            if (typeof window.unregisterEscapeHandler === 'function') {
                window.unregisterEscapeHandler(escapeReg.id);
            }
            escapeReg = null;
        }
        if (searchInput) {
            setSearchFocusRingVisible(false);
            searchInput.value = '';
            filterOptions();
        }
        clearTypeAhead();
    };

    const handleDocumentClick = (event) => {
        if (!wrapper.contains(event.target)) {
            closeMenu();
        }
    };

    const handleKeydown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            if (typeof event.stopImmediatePropagation === 'function') {
                event.stopImmediatePropagation();
            }
            event.stopPropagation();
            closeMenu();
            trigger.focus();
        }
    };

    wrapper._closeMenu = closeMenu;

    const openMenu = ({ showSearchFocusRing = false } = {}) => {
        document.querySelectorAll('.admin-select.open').forEach((openWrapper) => {
            if (openWrapper !== wrapper && typeof openWrapper._closeMenu === 'function') {
                openWrapper._closeMenu();
            }
        });
        wrapper.classList.add('open');
        menu.classList.add('open');
        trigger.setAttribute('aria-expanded', 'true');
        document.addEventListener('click', handleDocumentClick, true);
        document.addEventListener('keydown', handleKeydown, true);
        if (!escapeReg && typeof window.registerEscapeHandler === 'function') {
            escapeReg = window.registerEscapeHandler({
                id: `admin-select-escape-${triggerId}`,
                priority: 200,
                isActive: () => wrapper.classList.contains('open'),
                close: () => {
                    closeMenu();
                    trigger.focus();
                },
            });
        }
        clearTypeAhead();
        if (searchInput) {
            setSearchFocusRingVisible(showSearchFocusRing);
            const shouldAutoFocusSearchInput =
                searchAutoFocusEnabled &&
                !(disableMobileSearchAutoFocus && isLikelyMobileViewport());
            if (shouldAutoFocusSearchInput) {
                searchInput.focus();
                return;
            }
        }
        focusSelectedOption();
    };

    trigger.addEventListener('click', (event) => {
        event.preventDefault();
        if (wrapper.classList.contains('open')) {
            closeMenu();
        } else {
            openMenu({ showSearchFocusRing: event.detail === 0 });
        }
    });

    trigger.addEventListener('keydown', (event) => {
        if (handleTypeAhead(event)) {
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!wrapper.classList.contains('open')) {
                openMenu({ showSearchFocusRing: true });
            }
        } else if (event.key === 'Tab' && wrapper.classList.contains('open')) {
            closeMenu();
        }
    });

    const focusSelectedOption = () => {
        const selected = menu.querySelector('.admin-select-option.selected:not([hidden])');
        const fallback = menu.querySelector('.admin-select-option:not([hidden])');
        focusOption(selected || fallback);
    };

    menu.addEventListener('keydown', (event) => {
        if (searchInput && event.target === searchInput) {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                focusSelectedOption();
            } else if (event.key === 'Enter' && searchInput.value.trim()) {
                // Let a search user confirm the top filtered result directly
                // from the field instead of reaching for ArrowDown first.
                const firstVisibleOption = getFocusableOptions()[0];
                if (firstVisibleOption) {
                    event.preventDefault();
                    firstVisibleOption.click();
                }
            } else if (event.key === 'Tab') {
                closeMenu();
            }
            return;
        }

        if (handleTypeAhead(event)) {
            return;
        }
        const focusableOptions = getFocusableOptions();
        if (!focusableOptions.length) {
            return;
        }

        const currentIndex = focusableOptions.indexOf(document.activeElement);
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            const nextIndex = currentIndex === -1 ? 0 : Math.min(focusableOptions.length - 1, currentIndex + 1);
            focusOption(focusableOptions[nextIndex]);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            const prevIndex = currentIndex <= 0 ? 0 : currentIndex - 1;
            focusOption(focusableOptions[prevIndex]);
        } else if (event.key === 'Home') {
            event.preventDefault();
            focusOption(focusableOptions[0]);
        } else if (event.key === 'End') {
            event.preventDefault();
            focusOption(focusableOptions[focusableOptions.length - 1]);
        } else if (event.key === 'Enter') {
            const activeOption = document.activeElement;
            if (activeOption && activeOption.classList.contains('admin-select-option')) {
                event.preventDefault();
                activeOption.click();
            }
        } else if (event.key === 'Tab') {
            closeMenu();
        }
    });

    if (searchInput) {
        menu.addEventListener('pointerdown', () => {
            setSearchFocusRingVisible(false);
        });
        searchInput.addEventListener('input', () => {
            filterOptions();
        });
    }

    rebuildOptionButtons();
    syncFromSelect();

    return { wrapper, triggerId, syncFromSelect, refreshOptions: rebuildOptionButtons };
}

function upgradeAdminSingleSelect(select, options) {
    if (!select || typeof initializeAdminSingleSelect !== 'function') {
        return null;
    }

    const existingMeta = select._singleSelect;
    if (existingMeta?.wrapper?.parentNode) {
        existingMeta.wrapper.parentNode.insertBefore(select, existingMeta.wrapper);
        existingMeta.wrapper.remove();
    }

    const parent = select.parentNode;
    if (!parent) {
        return null;
    }

    const meta = initializeAdminSingleSelect(select, options);
    select._singleSelect = meta;
    parent.appendChild(meta.wrapper);
    meta.syncFromSelect?.();
    return meta;
}

function upgradeAdminMultiSelect(select, options) {
    if (!select || typeof initializeAdminMultiSelect !== 'function') {
        return null;
    }

    const existingMeta = select._multiSelect;
    const shouldRestoreOpenMenu = Boolean(
        existingMeta?.isOpen?.() || existingMeta?.wrapper?.classList.contains('open')
    );
    if (existingMeta?.wrapper?.parentNode) {
        existingMeta.destroy?.();
        existingMeta.wrapper.parentNode.insertBefore(select, existingMeta.wrapper);
        existingMeta.wrapper.remove();
    }

    const parent = select.parentNode;
    if (!parent) {
        return null;
    }

    const meta = initializeAdminMultiSelect(select, options);
    select._multiSelect = meta;
    parent.appendChild(meta.wrapper);
    meta.syncFromSelect?.();
    if (shouldRestoreOpenMenu) {
        meta.openMenu?.();
    }
    return meta;
}

function initializeAdminMultiSelect(select, field) {
    const wrapper = document.createElement('div');
    wrapper.className = 'admin-multiselect';

    let typeBuffer = '';
    let lastTypeTs = 0;
    let remoteSearchTimer = null;
    let remoteRequestId = 0;
    let remoteOptionsLoaded = false;
    const searchConfig = field?.search || {};
    const remoteOptionsConfig = field?.metadata?.remote_options || null;
    const remoteOptionsUrl = typeof remoteOptionsConfig?.url === 'string'
        ? remoteOptionsConfig.url.trim()
        : '';
    const remoteOptionsLimit = Math.max(
        1,
        Math.min(Number(remoteOptionsConfig?.limit) || 100, 100),
    );
    const isSearchable = Boolean(field?.searchable || searchConfig.enabled);
    const noOptionsMessage =
        searchConfig.emptyMessage ||
        helperT('admin_no_options_available', 'No options available');
    const noSearchResultsMessage = searchConfig.noResultsMessage || '';
    const searchAutoFocusEnabled = searchConfig.autoFocus !== false;
    const disableMobileSearchAutoFocus = Boolean(searchConfig.disableMobileAutoFocus);

    const isLikelyMobileViewport = () => {
        if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
            return false;
        }
        return (
            window.matchMedia('(max-width: 768px)').matches ||
            window.matchMedia('(hover: none) and (pointer: coarse)').matches
        );
    };

    const triggerId = `${select.id || `${field.key}-multi`}-trigger`;
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.id = triggerId;
    trigger.className = 'admin-multiselect-trigger placeholder';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    const triggerLabel = document.createElement('span');
    triggerLabel.className = 'admin-multiselect-value';
    trigger.appendChild(triggerLabel);

    const triggerCaret = document.createElement('span');
    triggerCaret.className = 'admin-multiselect-caret';
    triggerCaret.innerHTML = getAdminIconMarkup('chevron');
    trigger.appendChild(triggerCaret);

    const menu = document.createElement('div');
    menu.className = 'admin-multiselect-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-multiselectable', 'true');
    menu.hidden = true;

    wrapper.append(trigger, menu);

    let searchInput = null;
    let searchContainer = null;
    if (isSearchable) {
        searchContainer = document.createElement('label');
        searchContainer.className = 'admin-multiselect-search';

        const searchIcon = document.createElement('span');
        searchIcon.className = 'admin-multiselect-search-icon';
        searchIcon.innerHTML = getAdminIconMarkup('magnifyingGlass');
        searchContainer.appendChild(searchIcon);

        searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.className = 'admin-multiselect-search-input';
        configureAdminSelectSearchInput(searchInput, searchConfig);
        searchInput.autocomplete = 'off';
        searchInput.spellcheck = false;
        searchContainer.appendChild(searchInput);

        menu.appendChild(searchContainer);
    }

    const selectAllActions = document.createElement('div');
    selectAllActions.className = 'admin-multiselect-actions';

    const selectAllBtn = document.createElement('button');
    selectAllBtn.type = 'button';
    selectAllBtn.className = 'admin-multiselect-action-btn';
    selectAllBtn.textContent = helperT('btn_select_all', 'Select All');

    const unselectAllBtn = document.createElement('button');
    unselectAllBtn.type = 'button';
    unselectAllBtn.className = 'admin-multiselect-action-btn';
    unselectAllBtn.textContent = helperT('admin_unselect_all', 'Unselect All');

    selectAllActions.append(selectAllBtn, unselectAllBtn);
    // "Select all" is ambiguous for a server-backed result window because it
    // cannot represent users outside the currently loaded page.
    selectAllActions.hidden = Boolean(remoteOptionsUrl);
    menu.appendChild(selectAllActions);

    const optionsContainer = document.createElement('div');
    optionsContainer.className = 'admin-multiselect-options';
    menu.appendChild(optionsContainer);

    const getFocusableOptions = () =>
        Array.from(optionsContainer.querySelectorAll('.admin-multiselect-option')).filter(
            (option) => !option.hidden
        );

    const clearKeyboardFocus = () => {
        optionsContainer
            .querySelectorAll('.admin-multiselect-option.keyboard-focus')
            .forEach((option) => {
                option.classList.remove('keyboard-focus');
            });
    };

    const focusOption = (option) => {
        if (!option) {
            return;
        }
        clearKeyboardFocus();
        option.classList.add('keyboard-focus');
        option.focus();
        option.scrollIntoView({ block: 'nearest' });
    };

    const clearTypeAhead = () => {
        typeBuffer = '';
        lastTypeTs = 0;
        clearKeyboardFocus();
    };

    const setSearchFocusRingVisible = (visible) => {
        if (!searchContainer) {
            return;
        }
        searchContainer.classList.toggle('admin-multiselect-search-show-focus', Boolean(visible));
    };

    const handleTypeAhead = (event) => {
        if (!wrapper.classList.contains('open')) {
            return false;
        }

        if (event.key === 'Enter') {
            const focused =
                optionsContainer.querySelector('.admin-multiselect-option.keyboard-focus') ||
                document.activeElement;
            if (focused && focused.classList.contains('admin-multiselect-option')) {
                event.preventDefault();
                focused.click();
                return true;
            }
            return false;
        }

        if (event.key === 'Escape') {
            event.preventDefault();
            closeMenu();
            trigger.focus();
            return true;
        }

        if (event.key.length !== 1 || event.metaKey || event.ctrlKey || event.altKey) {
            return false;
        }

        const now = Date.now();
        if (now - lastTypeTs > 700) {
            typeBuffer = '';
        }
        lastTypeTs = now;

        typeBuffer += event.key.toLowerCase();

        const options = getFocusableOptions();
        if (!options.length) {
            return false;
        }

        const keyboardTarget =
            optionsContainer.querySelector('.admin-multiselect-option.keyboard-focus') ||
            document.activeElement;
        const startIndex = options.indexOf(keyboardTarget);
        const ordered =
            startIndex >= 0
                ? [...options.slice(startIndex + 1), ...options.slice(0, startIndex + 1)]
                : options;

        let match = ordered.find((opt) =>
            (opt.textContent || '').trim().toLowerCase().startsWith(typeBuffer)
        );

        if (!match && typeBuffer.length > 1) {
            typeBuffer = event.key.toLowerCase();
            match = ordered.find((opt) =>
                (opt.textContent || '').trim().toLowerCase().startsWith(typeBuffer)
            );
        }

        if (match) {
            event.preventDefault();
            focusOption(match);
            return true;
        }

        return false;
    };

    const optionButtons = new Map();
    const selectOptions = new Map();
    const empty = document.createElement('div');
    empty.className = 'admin-multiselect-empty';

    const updateEmptyState = () => {
        if (!optionButtons.size) {
            empty.textContent = noOptionsMessage;
            if (!empty.parentNode) {
                optionsContainer.appendChild(empty);
            }
            return;
        }

        if (searchInput && searchInput.value.trim() && !getFocusableOptions().length && noSearchResultsMessage) {
            empty.textContent = noSearchResultsMessage;
            if (!empty.parentNode) {
                optionsContainer.appendChild(empty);
            }
            return;
        }

        empty.remove();
    };

    const filterOptions = () => {
        const searchTerm = (searchInput?.value || '').trim().toLowerCase();
        optionButtons.forEach((button) => {
            const matches =
                !searchTerm || (button.dataset.searchText || '').includes(searchTerm);
            button.hidden = !matches;
        });
        updateEmptyState();
    };

    const rebuildOptionButtons = () => {
        optionButtons.clear();
        selectOptions.clear();
        optionsContainer.innerHTML = '';

        Array.from(select.options).forEach((opt) => {
            const value = String(opt.value ?? '');
            selectOptions.set(value, opt);

            const optionButton = document.createElement('button');
            optionButton.type = 'button';
            optionButton.tabIndex = -1;
            optionButton.className = 'admin-multiselect-option';
            optionButton.dataset.value = value;
            optionButton.dataset.searchText = String(opt.textContent || value || '—').trim().toLowerCase();
            optionButton.setAttribute('role', 'option');
            optionButton.setAttribute('aria-selected', opt.selected ? 'true' : 'false');

            const checkbox = document.createElement('span');
            checkbox.className = 'admin-multiselect-checkbox';
            checkbox.innerHTML = getAdminIconMarkup('check'); 
            optionButton.appendChild(checkbox);

            const text = document.createElement('span');
            text.className = 'admin-multiselect-text';
            text.textContent = opt.textContent || value || '—';
            optionButton.appendChild(text);

            if (opt.selected) {
                optionButton.classList.add('selected');
            }

            optionButton.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                toggleValue(value);
            });

            optionButtons.set(value, optionButton);
            optionsContainer.appendChild(optionButton);
        });

        filterOptions();
    };

    select.classList.add('admin-multiselect-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    wrapper.appendChild(select);

    const placeholder = getFieldPlaceholder(field, helperT('admin_select_placeholder_multi', 'Select options...'));

    const updateSummary = () => {
        const selectedOptions = Array.from(select.selectedOptions);
        if (typeof field?.multiselectSummary === 'function') {
            const customSummary = field.multiselectSummary({
                selectedOptions,
                totalOptions: Array.from(select.options),
                select,
                trigger,
                helperT,
            });
            if (customSummary && typeof customSummary === 'object') {
                triggerLabel.textContent = customSummary.text || placeholder;
                trigger.classList.toggle('placeholder', Boolean(customSummary.placeholder));
                return;
            }
            if (typeof customSummary === 'string') {
                triggerLabel.textContent = customSummary || placeholder;
                trigger.classList.toggle('placeholder', !selectedOptions.length);
                return;
            }
        }

        if (!selectedOptions.length) {
            triggerLabel.textContent = placeholder;
            trigger.classList.add('placeholder');
            return;
        }

        trigger.classList.remove('placeholder');
        if (selectedOptions.length <= 2) {
            triggerLabel.textContent = selectedOptions.map((opt) => opt.textContent).join(', ');
            return;
        }
        triggerLabel.textContent = helperT('admin_selected_count', '{count} selected').replace('{count}', selectedOptions.length);
    };

    const updateOptionButton = (value, selected) => {
        const button = optionButtons.get(value);
        if (!button) {
            return;
        }
        button.classList.toggle('selected', selected);
        button.setAttribute('aria-selected', selected ? 'true' : 'false');
    };

    const toggleValue = (value) => {
        const option = selectOptions.get(value);
        if (!option) {
            return;
        }
        const nextSelected = !option.selected;
        option.selected = nextSelected;
        updateOptionButton(value, nextSelected);
        updateSummary();
        select.dispatchEvent(new Event('change', { bubbles: true }));
    };

    const syncFromSelect = () => {
        selectOptions.forEach((opt, value) => {
            updateOptionButton(value, Boolean(opt.selected));
        });
        updateSummary();
        filterOptions();
    };

    /**
     * Replace the transient remote result page while retaining every selected
     * option. Selected values must survive a new server search so the native
     * select continues to be the complete form-value source of truth.
     */
    const replaceRemoteOptions = (remoteOptions) => {
        const selectedByValue = new Map(
            Array.from(select.selectedOptions || []).map((option) => [
                String(option.value),
                String(option.textContent || option.value),
            ])
        );
        select.innerHTML = '';

        selectedByValue.forEach((label, value) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            option.selected = true;
            select.appendChild(option);
        });

        (Array.isArray(remoteOptions) ? remoteOptions : []).forEach((entry) => {
            const value = String(entry?.value ?? '').trim();
            if (!value || selectedByValue.has(value)) {
                return;
            }
            const option = document.createElement('option');
            option.value = value;
            option.textContent = String(entry?.label || value);
            select.appendChild(option);
        });

        rebuildOptionButtons();
        syncFromSelect();
    };

    const loadRemoteOptions = async (searchTerm = '') => {
        if (
            !remoteOptionsUrl
            || typeof window === 'undefined'
            || typeof window.authedFetch !== 'function'
        ) {
            return;
        }
        const requestId = ++remoteRequestId;
        const params = new URLSearchParams({
            offset: '0',
            limit: String(remoteOptionsLimit),
        });
        const normalizedSearch = String(searchTerm || '').trim();
        if (normalizedSearch) {
            params.set('search', normalizedSearch);
        }
        const separator = remoteOptionsUrl.includes('?') ? '&' : '?';
        try {
            const response = await window.authedFetch(
                `${remoteOptionsUrl}${separator}${params.toString()}`,
                { method: 'GET' },
            );
            if (!response.ok) {
                throw new Error(helperT('admin_request_failed', 'Request failed.'));
            }
            const page = await response.json();
            if (requestId !== remoteRequestId) {
                return;
            }
            replaceRemoteOptions(page?.options || []);
            remoteOptionsLoaded = true;
        } catch (error) {
            if (requestId !== remoteRequestId) {
                return;
            }
            remoteOptionsLoaded = false;
            console.error('Failed to load remote multi-select options', error);
            window.notifyError?.(error?.message || helperT('admin_request_failed', 'Request failed.'));
        }
    };

    const scheduleRemoteOptionsLoad = (searchTerm) => {
        if (!remoteOptionsUrl) {
            return;
        }
        if (remoteSearchTimer) {
            window.clearTimeout(remoteSearchTimer);
        }
        remoteSearchTimer = window.setTimeout(() => {
            remoteSearchTimer = null;
            loadRemoteOptions(searchTerm);
        }, 250);
    };

    select.addEventListener('change', syncFromSelect);

    selectAllBtn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        selectOptions.forEach((opt, value) => {
            opt.selected = true;
            updateOptionButton(value, true);
        });
        updateSummary();
        select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    unselectAllBtn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        selectOptions.forEach((opt, value) => {
            opt.selected = false;
            updateOptionButton(value, false);
        });
        updateSummary();
        select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    const closeMenu = () => {
        if (!wrapper.classList.contains('open')) {
            return;
        }
        wrapper.classList.remove('open');
        menu.classList.remove('open');
        menu.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('click', handleDocumentClick, true);
        document.removeEventListener('keydown', handleKeydown, true);
        if (searchInput) {
            if (remoteOptionsUrl && searchInput.value.trim()) {
                // The next open should restore the default bounded page rather
                // than treating the last search page as the complete list.
                remoteOptionsLoaded = false;
                remoteRequestId += 1;
                if (remoteSearchTimer) {
                    window.clearTimeout(remoteSearchTimer);
                    remoteSearchTimer = null;
                }
            }
            setSearchFocusRingVisible(false);
            searchInput.value = '';
            filterOptions();
        }
        clearTypeAhead();
    };

    const handleDocumentClick = (event) => {
        if (!wrapper.contains(event.target)) {
            closeMenu();
        }
    };

    const handleKeydown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeMenu();
            trigger.focus();
        }
    };

    wrapper._closeMenu = closeMenu;

    const openMenu = ({ showSearchFocusRing = false } = {}) => {
        document.querySelectorAll('.admin-multiselect.open').forEach((openWrapper) => {
            if (openWrapper !== wrapper && typeof openWrapper._closeMenu === 'function') {
                openWrapper._closeMenu();
            }
        });
        menu.hidden = false;
        wrapper.classList.add('open');
        menu.classList.add('open');
        trigger.setAttribute('aria-expanded', 'true');
        document.addEventListener('click', handleDocumentClick, true);
        document.addEventListener('keydown', handleKeydown, true);
        clearTypeAhead();
        if (remoteOptionsUrl && !remoteOptionsLoaded) {
            loadRemoteOptions(searchInput?.value || '');
        }
        if (searchInput) {
            setSearchFocusRingVisible(showSearchFocusRing);
            const shouldAutoFocusSearchInput =
                searchAutoFocusEnabled &&
                !(disableMobileSearchAutoFocus && isLikelyMobileViewport());
            if (shouldAutoFocusSearchInput) {
                searchInput.focus();
                return;
            }
            focusInitialOption();
            return;
        }
        focusInitialOption();
    };

    trigger.addEventListener('click', (event) => {
        event.preventDefault();
        if (wrapper.classList.contains('open')) {
            closeMenu();
        } else {
            openMenu({ showSearchFocusRing: event.detail === 0 });
        }
    });

    trigger.addEventListener('keydown', (event) => {
        if (handleTypeAhead(event)) {
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!wrapper.classList.contains('open')) {
                openMenu({ showSearchFocusRing: true });
            }
        } else if (event.key === 'Tab' && wrapper.classList.contains('open')) {
            closeMenu();
        }
    });

    const focusInitialOption = () => {
        const firstSelected = optionsContainer.querySelector('.admin-multiselect-option.selected:not([hidden])');
        const fallback = optionsContainer.querySelector('.admin-multiselect-option:not([hidden])');
        focusOption(firstSelected || fallback);
    };

    menu.addEventListener('keydown', (event) => {
        if (searchInput && event.target === searchInput) {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                focusInitialOption();
            } else if (event.key === 'Tab') {
                closeMenu();
            }
            return;
        }

        if (handleTypeAhead(event)) {
            return;
        }
        const focusableOptions = getFocusableOptions();
        if (!focusableOptions.length) {
            return;
        }

        const currentIndex = focusableOptions.indexOf(document.activeElement);
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            const nextIndex = currentIndex === -1 ? 0 : Math.min(focusableOptions.length - 1, currentIndex + 1);
            focusableOptions[nextIndex].focus();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            const prevIndex = currentIndex <= 0 ? 0 : currentIndex - 1;
            focusableOptions[prevIndex].focus();
        } else if (event.key === 'Home') {
            event.preventDefault();
            focusableOptions[0].focus();
        } else if (event.key === 'End') {
            event.preventDefault();
            focusableOptions[focusableOptions.length - 1].focus();
        } else if (event.key === 'Tab') {
            closeMenu();
        }
    });

    if (searchInput) {
        menu.addEventListener('pointerdown', () => {
            setSearchFocusRingVisible(false);
        });
        searchInput.addEventListener('input', () => {
            filterOptions();
            scheduleRemoteOptionsLoad(searchInput.value);
        });
    }

    rebuildOptionButtons();
    syncFromSelect();

    const destroy = () => {
        closeMenu();
        remoteRequestId += 1;
        if (remoteSearchTimer) {
            window.clearTimeout(remoteSearchTimer);
            remoteSearchTimer = null;
        }
        select.removeEventListener('change', syncFromSelect);
    };

    return {
        wrapper,
        triggerId,
        syncFromSelect,
        refreshOptions: rebuildOptionButtons,
        openMenu,
        closeMenu,
        isOpen: () => wrapper.classList.contains('open'),
        destroy,
    };
}

if (typeof window !== 'undefined') {
    window.initializeAdminSingleSelect = window.initializeAdminSingleSelect || initializeAdminSingleSelect;
    window.upgradeAdminSingleSelect = window.upgradeAdminSingleSelect || upgradeAdminSingleSelect;
    window.initializeAdminMultiSelect = window.initializeAdminMultiSelect || initializeAdminMultiSelect;
    window.upgradeAdminMultiSelect = window.upgradeAdminMultiSelect || upgradeAdminMultiSelect;
}
