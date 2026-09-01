(function () {
    'use strict';

    // Custom format controls are deliberately built on top of the existing
    // select elements. Keeping the select as the source of truth lets all
    // download handlers continue reading `.value` while the browser-native UI
    // is replaced by a consistent, fully styled and keyboard-friendly menu.
    const customFormatControls = new WeakMap();
    let openFormatControl = null;
    let outsideListenersBound = false;

    function sanitizeDownloadFilename(filename, fallback = 'download') {
        const normalized = String(filename || '').trim().slice(0, 180);
        const safe = normalized.replace(/[\/:*?"<>|]/g, '-').replace(/\s+/g, ' ');
        return safe || fallback;
    }

    function saveBlobAsFile(blob, filename, revokeDelayMs = 1000) {
        if (!blob || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
            return;
        }

        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = sanitizeDownloadFilename(filename);
        document.body.appendChild(link);
        link.click();
        link.remove();

        setTimeout(() => {
            if (typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
                URL.revokeObjectURL(objectUrl);
            }
        }, Math.max(0, Number(revokeDelayMs) || 0));
    }

    function getSelectedDownloadFormat(selectEl, fallback = '') {
        const value = String(selectEl?.value || fallback || '').trim();
        return value || fallback;
    }

    /** Close the currently open custom format menu, if there is one. */
    function closeOpenFormatMenu({ restoreFocus = false } = {}) {
        if (!openFormatControl) return;
        const control = openFormatControl;
        openFormatControl = null;
        control.close({ restoreFocus });
    }

    /**
     * Bind the one set of document-level dismissal listeners shared by every
     * custom format control. Pointer dismissal uses capture so clicks elsewhere
     * close the menu even when another component stops bubbling.
     */
    function bindOutsideFormatMenuListeners() {
        if (outsideListenersBound) return;
        outsideListenersBound = true;

        document.addEventListener('pointerdown', (event) => {
            if (!openFormatControl || openFormatControl.wrapper.contains(event.target)) return;
            closeOpenFormatMenu();
        }, true);

        window.addEventListener('blur', () => closeOpenFormatMenu());
    }

    /**
     * Upgrade a native download-format select into an accessible custom menu.
     * The returned controller exposes `sync()` for callers that change select
     * options, disabled state, translations, or visibility programmatically.
     */
    function enhanceDownloadFormatSelect(selectEl, options = {}) {
        if (!selectEl) return null;
        const existing = customFormatControls.get(selectEl);
        if (existing) {
            existing.sync();
            return existing;
        }

        const wrapper = options.wrapper || selectEl.closest('.slide-presentation-preview-download-controls');
        const downloadButton = options.downloadButton
            || wrapper?.querySelector('.om-button, .slide-presentation-preview-download-btn');
        if (!wrapper || !downloadButton) return null;

        const trigger = document.createElement('button');
        const label = document.createElement('span');
        const chevron = document.createElement('span');
        const menu = document.createElement('div');
        const baseId = selectEl.id || `download-format-${Math.random().toString(36).slice(2)}`;

        trigger.type = 'button';
        trigger.className = 'custom-download-format-trigger';
        trigger.id = `${baseId}-trigger`;
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.setAttribute('aria-controls', `${baseId}-menu`);

        label.className = 'custom-download-format-label';
        chevron.className = 'custom-download-format-chevron';
        chevron.setAttribute('aria-hidden', 'true');
        trigger.append(label, chevron);

        menu.className = 'custom-download-format-menu';
        menu.id = `${baseId}-menu`;
        menu.setAttribute('role', 'listbox');
        menu.setAttribute('aria-labelledby', trigger.id);
        menu.hidden = true;

        wrapper.classList.add('custom-download-control');
        selectEl.classList.add('custom-download-native-select');
        selectEl.setAttribute('aria-hidden', 'true');
        selectEl.tabIndex = -1;
        wrapper.insertBefore(trigger, selectEl);
        wrapper.appendChild(menu);

        /** Return the menu items in their current visual order. */
        const getMenuItems = () => Array.from(menu.querySelectorAll('.custom-download-format-option'));

        /** Focus an option without allowing the header beneath it to scroll. */
        const focusItem = (item) => {
            if (!item) return;
            item.focus({ preventScroll: true });
        };

        const controller = {
            wrapper,
            select: selectEl,
            trigger,
            menu,

            /** Close this menu and optionally return focus to its trigger. */
            close({ restoreFocus = false } = {}) {
                menu.hidden = true;
                wrapper.classList.remove('is-open');
                trigger.setAttribute('aria-expanded', 'false');
                if (openFormatControl === controller) openFormatControl = null;
                if (restoreFocus && !trigger.disabled) trigger.focus({ preventScroll: true });
            },

            /** Open below the split button and focus the selected option. */
            open({ focusSelected = false, focusLast = false } = {}) {
                if (trigger.disabled || trigger.hidden) return;
                if (openFormatControl && openFormatControl !== controller) {
                    openFormatControl.close();
                }
                openFormatControl = controller;
                menu.hidden = false;
                wrapper.classList.add('is-open');
                trigger.setAttribute('aria-expanded', 'true');
                if (focusSelected || focusLast) {
                    window.requestAnimationFrame(() => {
                        const items = getMenuItems();
                        const selected = items.find((item) => item.getAttribute('aria-selected') === 'true');
                        focusItem(focusLast ? items.at(-1) : (selected || items[0]));
                    });
                }
            },

            /** Rebuild option rows and mirror all state from the source select. */
            sync() {
                const selectOptions = Array.from(selectEl.options || []);
                const selectedOption = selectOptions.find((option) => option.value === selectEl.value)
                    || selectOptions.find((option) => option.selected)
                    || selectOptions[0];
                const isUnavailable = Boolean(selectEl.disabled) || selectOptions.length === 0;
                const accessibleLabel = selectEl.getAttribute('aria-label');

                label.textContent = selectedOption?.textContent?.trim() || '';
                trigger.disabled = isUnavailable;
                trigger.setAttribute('aria-disabled', isUnavailable ? 'true' : 'false');
                if (accessibleLabel) trigger.setAttribute('aria-label', accessibleLabel);
                else trigger.removeAttribute('aria-label');

                // Canvas can hide the format picker while leaving the adjacent
                // primary download action available in this shared wrapper.
                // Only the picker trigger and its menu mirror the select.
                wrapper.hidden = false;
                trigger.hidden = Boolean(selectEl.hidden);
                wrapper.classList.toggle('is-disabled', isUnavailable);

                menu.replaceChildren(...selectOptions.map((option, index) => {
                    const item = document.createElement('button');
                    const itemLabel = document.createElement('span');
                    const check = document.createElement('span');
                    const selected = option === selectedOption;

                    item.type = 'button';
                    item.className = 'custom-download-format-option';
                    item.id = `${baseId}-option-${index}`;
                    item.dataset.value = option.value;
                    item.setAttribute('role', 'option');
                    item.setAttribute('aria-selected', selected ? 'true' : 'false');
                    item.disabled = Boolean(option.disabled);

                    itemLabel.className = 'custom-download-format-option-label';
                    itemLabel.textContent = option.textContent?.trim() || option.value;
                    check.className = 'custom-download-format-check';
                    check.setAttribute('aria-hidden', 'true');
                    item.append(itemLabel, check);
                    return item;
                }));

                if (isUnavailable || trigger.hidden) controller.close();
            },
        };

        trigger.addEventListener('click', () => {
            if (menu.hidden) controller.open();
            else controller.close();
        });

        trigger.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !menu.hidden) {
                event.preventDefault();
                controller.close({ restoreFocus: true });
                return;
            }
            if (event.key === 'Tab' && !menu.hidden) {
                controller.close();
                return;
            }
            if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
            event.preventDefault();
            controller.open({ focusSelected: event.key === 'ArrowDown', focusLast: event.key === 'ArrowUp' });
        });

        // Activating the dedicated download half should always leave the
        // compact header in its resting state, even if the menu was open.
        downloadButton.addEventListener('click', () => controller.close());

        menu.addEventListener('click', (event) => {
            const item = event.target.closest('.custom-download-format-option');
            if (!item || item.disabled) return;
            selectEl.value = item.dataset.value || '';
            selectEl.dispatchEvent(new Event('change', { bubbles: true }));
            controller.sync();
            controller.close({ restoreFocus: true });
        });

        menu.addEventListener('keydown', (event) => {
            const items = getMenuItems().filter((item) => !item.disabled);
            const currentIndex = items.indexOf(document.activeElement);
            let nextItem = null;

            if (event.key === 'ArrowDown') nextItem = items[(currentIndex + 1) % items.length];
            if (event.key === 'ArrowUp') nextItem = items[(currentIndex - 1 + items.length) % items.length];
            if (event.key === 'Home') nextItem = items[0];
            if (event.key === 'End') nextItem = items.at(-1);
            if (event.key === 'Escape') {
                event.preventDefault();
                controller.close({ restoreFocus: true });
                return;
            }
            if (event.key === 'Tab') {
                controller.close();
                return;
            }
            if (nextItem) {
                event.preventDefault();
                focusItem(nextItem);
            }
        });

        // Option labels are translated in place and Canvas replaces its option
        // set when the artifact type changes, so observe both kinds of updates.
        const observer = new MutationObserver(() => controller.sync());
        observer.observe(selectEl, {
            attributes: true,
            attributeFilter: ['disabled', 'hidden', 'aria-label'],
            childList: true,
            characterData: true,
            subtree: true,
        });

        customFormatControls.set(selectEl, controller);
        bindOutsideFormatMenuListeners();
        controller.sync();
        return controller;
    }

    /** Sync an already enhanced custom format select after direct DOM updates. */
    function syncDownloadFormatSelect(selectEl) {
        customFormatControls.get(selectEl)?.sync();
    }

    function getButtonLabelElement(buttonEl, labelSelector = 'span') {
        if (!buttonEl || typeof buttonEl.querySelector !== 'function') {
            return null;
        }
        return buttonEl.querySelector(labelSelector);
    }

    function setButtonLabel(buttonEl, label, options = {}) {
        if (!buttonEl || label === undefined || label === null) {
            return;
        }

        const labelEl = getButtonLabelElement(buttonEl, options.labelSelector);
        if (labelEl) {
            labelEl.textContent = String(label);
            return;
        }

        if (options.defaultHtml !== undefined) {
            buttonEl.innerHTML = options.defaultHtml;
            const restoredLabelEl = getButtonLabelElement(buttonEl, options.labelSelector);
            if (restoredLabelEl) {
                restoredLabelEl.textContent = String(label);
            }
        }
    }

    function setElementDisabled(element, disabled, options = {}) {
        if (!element) {
            return;
        }

        const isDisabled = Boolean(disabled);
        if ('disabled' in element) {
            element.disabled = isDisabled;
        }
        if (options.disabledClass) {
            element.classList.toggle(options.disabledClass, isDisabled);
        }
        if (options.useAriaDisabled !== false) {
            element.setAttribute('aria-disabled', isDisabled ? 'true' : 'false');
        }
        if (options.manageTabIndex) {
            element.tabIndex = isDisabled ? -1 : 0;
        }
        syncDownloadFormatSelect(element);
    }

    function setDownloadControlsEnabled(options = {}) {
        const enabled = Boolean(options.enabled);
        setElementDisabled(options.button, !enabled, {
            disabledClass: options.disabledClass,
            manageTabIndex: options.manageTabIndex,
            useAriaDisabled: options.useAriaDisabled,
        });
        setElementDisabled(options.select, !enabled, { useAriaDisabled: false });

        if (options.defaultHtml !== undefined && options.button && !enabled) {
            options.button.innerHTML = options.defaultHtml;
        }
        if (options.label !== undefined) {
            setButtonLabel(options.button, options.label, options);
        }
    }

    function setDownloadBusy(options = {}) {
        const busy = Boolean(options.busy);
        const disabled = busy || !Boolean(options.enabled);
        const button = options.button;

        setElementDisabled(button, disabled, {
            disabledClass: options.disabledClass,
            manageTabIndex: options.manageTabIndex,
            useAriaDisabled: options.useAriaDisabled,
        });
        setElementDisabled(options.select, disabled, { useAriaDisabled: false });

        if (button) {
            button.classList.toggle(options.busyClass || 'is-busy', busy);
            if (busy) {
                button.setAttribute('aria-busy', 'true');
            } else {
                button.removeAttribute('aria-busy');
                if (options.defaultHtml !== undefined) {
                    button.innerHTML = options.defaultHtml;
                }
            }
            setButtonLabel(button, busy ? options.busyLabel : options.idleLabel, options);
        }
    }

    async function fetchBlob(downloadUrl, options = {}) {
        const requestFn = options.fetcher || (typeof window.authedFetch === 'function' ? window.authedFetch : fetch);
        const response = await requestFn(downloadUrl);
        if (!response || !response.ok) {
            const status = response?.status || '';
            const statusText = response?.statusText || '';
            const message = typeof options.errorMessage === 'function'
                ? options.errorMessage(response)
                : (options.errorMessage || `Download failed: ${status} ${statusText}`.trim());
            throw new Error(message);
        }
        return response.blob();
    }

    async function downloadBlobFromUrl(downloadUrl, filename, options = {}) {
        const blob = await fetchBlob(downloadUrl, options);
        saveBlobAsFile(blob, filename, options.revokeDelayMs);
        return blob;
    }

    window.chatDownloadControls = {
        closeOpenFormatMenu,
        downloadBlobFromUrl,
        enhanceDownloadFormatSelect,
        fetchBlob,
        getSelectedDownloadFormat,
        sanitizeDownloadFilename,
        saveBlobAsFile,
        setDownloadBusy,
        setDownloadControlsEnabled,
        syncDownloadFormatSelect,
    };
})();
