/**
 * Shared helper for reliable file picking flows.
 *
 * Browsers can be inconsistent when a file input is created only for a single
 * click and never attached to the DOM. Keeping the input mounted avoids missed
 * `change` events and also lets us handle cancellation paths consistently.
 */
(function () {
    /**
     * Create a persistent hidden file picker and expose an `open()` helper that
     * resolves with the selected file (or files when `multiple` is enabled).
     *
     * @param {Object} options
     * @param {string} [options.id] Stable DOM id for the hidden input.
     * @param {string} [options.accept] Accept attribute value.
     * @param {boolean} [options.multiple] Whether multiple file selection is allowed.
     * @returns {{ input: HTMLInputElement, open: () => Promise<File|File[]|null>, destroy: () => void } | null}
     */
    function createPersistentFilePicker({ id, accept = '', multiple = false } = {}) {
        if (typeof document === 'undefined' || typeof document.createElement !== 'function') {
            return null;
        }

        const existingInput = id ? document.getElementById(id) : null;
        const hasNativeInputClass = typeof HTMLInputElement !== 'undefined';
        const isInputElement = hasNativeInputClass
            ? existingInput instanceof HTMLInputElement
            : String(existingInput?.tagName || '').toLowerCase() === 'input';
        const input = isInputElement ? existingInput : document.createElement('input');

        // Configure the input every time so callers can safely reuse ids.
        input.type = 'file';
        input.accept = accept;
        input.multiple = Boolean(multiple);
        input.hidden = true;
        input.tabIndex = -1;
        input.setAttribute('aria-hidden', 'true');

        if (id) {
            input.id = id;
        }

        if (!input.parentNode && document.body) {
            document.body.appendChild(input);
        }

        let settled = true;
        let resolvePending = null;
        let changeHandler = null;
        let cancelHandler = null;
        let focusHandler = null;

        /**
         * Remove transient listeners for the current selection cycle.
         */
        const cleanupSelectionListeners = () => {
            if (changeHandler) {
                input.removeEventListener('change', changeHandler);
                changeHandler = null;
            }
            if (cancelHandler) {
                input.removeEventListener('cancel', cancelHandler);
                cancelHandler = null;
            }
            if (focusHandler && typeof window !== 'undefined') {
                window.removeEventListener('focus', focusHandler);
                focusHandler = null;
            }
        };

        /**
         * Resolve the current picker promise exactly once.
         *
         * @param {File|File[]|null} value
         */
        const settle = (value) => {
            if (settled) {
                return;
            }

            settled = true;
            cleanupSelectionListeners();

            const resolver = resolvePending;
            resolvePending = null;
            resolver?.(value);
        };

        /**
         * Start a new file-picking cycle. The existing input stays mounted so the
         * browser can reliably deliver the resulting selection back to us.
         *
         * @returns {Promise<File|File[]|null>}
         */
        const open = () => new Promise((resolve) => {
            // If a previous cycle is still hanging because the browser never sent
            // a completion signal, close it before opening a fresh picker.
            settle(multiple ? [] : null);

            settled = false;
            resolvePending = resolve;

            // Reset the value so selecting the exact same file still emits `change`.
            input.value = '';

            changeHandler = () => {
                const files = Array.from(input.files || []);
                settle(multiple ? files : (files[0] || null));
            };

            cancelHandler = () => {
                settle(multiple ? [] : null);
            };

            focusHandler = () => {
                const timeoutFn = typeof window?.setTimeout === 'function' ? window.setTimeout.bind(window) : setTimeout;
                timeoutFn(() => {
                    if (settled) {
                        return;
                    }

                    const files = Array.from(input.files || []);
                    if (files.length === 0) {
                        settle(multiple ? [] : null);
                    }
                }, 250);
            };

            input.addEventListener('change', changeHandler, { once: true });
            input.addEventListener('cancel', cancelHandler, { once: true });
            if (typeof window !== 'undefined') {
                window.addEventListener('focus', focusHandler, { once: true });
            }

            input.click();
        });

        /**
         * Tear down the picker when a page no longer needs it.
         */
        const destroy = () => {
            settle(multiple ? [] : null);
            if (input.parentNode) {
                input.parentNode.removeChild(input);
            }
        };

        return {
            input,
            open,
            destroy,
        };
    }

    window.createPersistentFilePicker = createPersistentFilePicker;
})();
