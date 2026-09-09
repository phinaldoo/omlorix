(function () {
    'use strict';

    // Hidden selects retain each preview's format options and state. The only
    // visible control is the shared icon button and transient dropdown menu.
    const formatMenus = new WeakMap();
    let openFormatControl = null;

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

    /** Close the currently open download format menu, if there is one. */
    function closeOpenFormatMenu({ restoreFocus = false } = {}) {
        if (!openFormatControl) return;
        const control = openFormatControl;
        openFormatControl = null;
        control.close({ restoreFocus });
    }

    /** Bind a single download icon to shared format actions (or direct download). */
    function bindDownloadFormatMenu(selectEl, { downloadButton, onDownload } = {}) {
        if (!selectEl || !downloadButton || typeof onDownload !== 'function') return null;
        const existing = formatMenus.get(selectEl);
        if (existing) return existing;
        selectEl.style.display = 'none';
        selectEl.setAttribute('aria-hidden', 'true');
        selectEl.tabIndex = -1;
        let menu = null;
        let downloading = false;
        const formats = () => Array.from(selectEl.options).filter(option => !option.hidden);
        const hasMenu = () => !selectEl.hidden && formats().length > 1;
        const controller = {
            close(detail = {}) {
                menu?.close(detail);
                menu = null;
                if (openFormatControl === controller) openFormatControl = null;
            },
            sync() {
                if (hasMenu()) downloadButton.setAttribute('aria-haspopup', 'menu');
                else downloadButton.removeAttribute('aria-haspopup');
                if (!menu?.isOpen()) downloadButton.setAttribute('aria-expanded', 'false');
                controller.close();
            },
        };
        const download = async event => {
            if (downloading) return;
            downloading = true;
            controller.close();
            try { await onDownload(event); }
            finally {
                downloading = false;
                if (document.activeElement === document.body && downloadButton.isConnected && downloadButton.getClientRects().length) {
                    downloadButton.focus({ preventScroll: true });
                }
            }
        };
        downloadButton.addEventListener('click', event => {
            event.preventDefault();
            if (downloading || downloadButton.disabled || downloadButton.getAttribute('aria-disabled') === 'true') return;
            if (menu?.isOpen()) { controller.close({ restoreFocus: true }); return; }
            if (!hasMenu()) { void download(event); return; }
            closeOpenFormatMenu();
            openFormatControl = controller;
            menu = window.openDropdownMenu({
                trigger: downloadButton,
                ariaLabel: selectEl.getAttribute('aria-label') || downloadButton.getAttribute('aria-label'),
                items: formats().map(option => ({
                    value: option.value, label: option.textContent.trim(), disabled: option.disabled || selectEl.disabled,
                })),
                onSelect: (item, selectionEvent) => {
                    selectEl.value = item.value;
                    selectEl.dispatchEvent(new Event('change', { bubbles: true }));
                    return download(selectionEvent);
                },
            });
        });
        // Canvas replaces its options as the file type changes; localization
        // and availability updates must also invalidate an open menu.
        const observer = new MutationObserver(() => controller.sync());
        observer.observe(selectEl, {
            attributes: true, attributeFilter: ['disabled', 'hidden', 'aria-label'],
            childList: true, characterData: true, subtree: true,
        });
        formatMenus.set(selectEl, controller);
        controller.sync();
        return controller;
    }

    /** Refresh the download menu after its format options change. */
    function syncDownloadFormatSelect(selectEl) {
        formatMenus.get(selectEl)?.sync();
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
        const disabled = busy || !options.enabled;
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
        bindDownloadFormatMenu,
        fetchBlob,
        getSelectedDownloadFormat,
        sanitizeDownloadFilename,
        saveBlobAsFile,
        setDownloadBusy,
        setDownloadControlsEnabled,
        syncDownloadFormatSelect,
    };
})();
