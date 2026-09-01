/*
 * Omlorix keyboard command system.
 *
 * This module deliberately keeps the global key surface small. Browser- and
 * operating-system-reserved combinations are left alone; less frequent app
 * actions live in the searchable command palette instead. The pure helpers at
 * the top are exported in CommonJS environments so the matching rules can be
 * tested without a browser DOM.
 */
(function initShortcutModule(root) {
    'use strict';

    /**
     * Detect whether the platform convention uses Command or Control as the
     * primary application modifier.
     *
     * @param {Navigator|Record<string, unknown>} navigatorLike Browser navigator.
     * @returns {{usesMeta: boolean, platform: string}}
     */
    function detectShortcutPlatform(navigatorLike = {}) {
        const uaDataPlatform = navigatorLike?.userAgentData?.platform || '';
        const platform = navigatorLike?.platform || uaDataPlatform || '';
        const userAgent = navigatorLike?.userAgent || uaDataPlatform || '';
        const usesMeta = /Mac|iPad|iPhone|iPod/i.test(platform)
            || /Mac|iPad|iPhone|iPod/i.test(userAgent);
        return {
            usesMeta,
            platform: platform || (usesMeta ? 'apple' : 'other'),
        };
    }

    /**
     * Return whether an event uses exactly the platform's primary modifier.
     * Requiring the opposite modifier to be released avoids accidental matches
     * with AltGraph and multi-modifier editor commands.
     *
     * @param {KeyboardEvent|Record<string, unknown>} event Keyboard-like event.
     * @param {{usesMeta: boolean}} platform Platform convention.
     * @returns {boolean}
     */
    function hasPrimaryModifier(event, platform) {
        if (!event) return false;
        return platform.usesMeta
            ? Boolean(event.metaKey && !event.ctrlKey)
            : Boolean(event.ctrlKey && !event.metaKey);
    }

    /**
     * Match a keyboard event against one declarative binding.
     *
     * @param {KeyboardEvent|Record<string, unknown>} event Keyboard-like event.
     * @param {Record<string, unknown>} binding Shortcut binding.
     * @param {{usesMeta: boolean}} platform Platform convention.
     * @returns {boolean}
     */
    function matchesBinding(event, binding, platform) {
        if (!event || !binding) return false;
        if (binding.primary === true && !hasPrimaryModifier(event, platform)) return false;
        if (binding.primary !== true && (event.metaKey || event.ctrlKey)) return false;

        const expectedAlt = binding.alt === true;
        if (Boolean(event.altKey) !== expectedAlt) return false;

        const expectedShift = binding.shift === true;
        if (Boolean(event.shiftKey) !== expectedShift && !binding.allowShiftForCharacter) return false;

        if (binding.code) {
            return String(event.code || '').toLowerCase() === String(binding.code).toLowerCase();
        }
        return String(event.key || '').toLowerCase() === String(binding.key || '').toLowerCase();
    }

    /**
     * Identify rich editors and terminal surfaces whose local key maps always
     * take precedence over application shortcuts.
     *
     * @param {Element|null} target Event target.
     * @returns {boolean}
     */
    function isProtectedEditorTarget(target) {
        if (!target || typeof target.closest !== 'function') return false;
        return Boolean(target.closest([
            '[contenteditable="true"]',
            '[contenteditable="plaintext-only"]',
            '.CodeMirror',
            '.cm-editor',
            '.monaco-editor',
            '[role="textbox"][aria-multiline="true"]',
        ].join(', ')));
    }

    /** Normalize text for predictable, accent-insensitive command searching. */
    function normalizeSearchText(value) {
        return String(value || '')
            .normalize('NFKD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLocaleLowerCase()
            .trim();
    }

    const exportedCore = {
        detectShortcutPlatform,
        hasPrimaryModifier,
        matchesBinding,
        isProtectedEditorTarget,
        normalizeSearchText,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = exportedCore;
    }
    if (root) {
        root.ChatShortcutCore = exportedCore;
    }
    if (!root?.document) return;

    const document = root.document;
    const shortcutPlatform = detectShortcutPlatform(root.navigator || {});
    const commandRegistry = new Map();
    const state = {
        paletteOpen: false,
        paletteClosing: false,
        paletteCloseSequence: 0,
        paletteCloseTimer: 0,
        results: [],
        activeIndex: 0,
        returnFocus: null,
        chatSearchController: null,
        chatSearchTimer: 0,
        chatSearchSequence: 0,
        palette: null,
        input: null,
        resultsHost: null,
        status: null,
        resultCount: null,
        bodyHadModalOpen: false,
    };

    const t = (key, fallback) => (
        typeof root.getTranslation === 'function'
            ? root.getTranslation(key, fallback)
            : fallback
    );

    const tf = (key, fallback, values = {}) => {
        if (typeof root.formatTranslation === 'function') {
            return root.formatTranslation(key, fallback, values);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => (
            values[token] == null ? `{${token}}` : String(values[token])
        ));
    };

    /** Select the locale's explicit plural key, falling back to `other`. */
    const pluralTf = (baseKey, count, oneFallback, otherFallback) => {
        let category = Number(count) === 1 ? 'one' : 'other';
        try {
            category = new Intl.PluralRules(document.documentElement?.lang || 'en').select(Math.abs(Number(count) || 0));
        } catch (_error) {
            // The one/other fallback above covers hosts without Intl support.
        }
        const fallback = category === 'one' ? oneFallback : otherFallback;
        return tf(`${baseKey}_${category}`, fallback, { count });
    };

    /**
     * Return true only for elements that are actually available to the user.
     *
     * Several Omlorix popovers stay in the layout while closed and use opacity
     * plus pointer-events instead of display or visibility. Treating those
     * dormant boxes as visible would make them incorrectly suppress every
     * application shortcut.
     */
    function isVisible(element) {
        if (!(element instanceof Element) || element.hidden) return false;
        const style = root.getComputedStyle(element);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (style.pointerEvents === 'none' || Number.parseFloat(style.opacity) === 0) return false;
        return element.getClientRects().length > 0;
    }

    /**
     * Determine whether an *open* foreground surface should own the keystroke.
     * ARIA roles describe a widget's semantics, not its current open state, so
     * bare menu/listbox roles must never be used as visibility selectors.
     */
    function hasBlockingSurface() {
        const candidates = document.querySelectorAll([
            '[role="dialog"][aria-modal="true"]',
            'dialog[open]',
            '[role="menu"].open',
            '[role="listbox"].open',
            '[role="menu"][aria-hidden="false"]',
            '[role="listbox"][aria-hidden="false"]',
            '.open [role="menu"]',
            '.open [role="listbox"]',
            '.show [role="menu"]',
            '.show [role="listbox"]',
            '.modal.open',
            '.modal.show',
            '.archived-chats-overlay.open',
            '.model-select-dropdown.open',
            '.bottom-sheet.open',
        ].join(', '));
        return Array.from(candidates).some((element) => (
            element !== state.palette && !state.palette?.contains(element) && isVisible(element)
        ));
    }

    function isChatSurfaceVisible() {
        return ['chatContainer', 'chatContainerWelcome'].some((id) => {
            const element = document.getElementById(id);
            return element ? isVisible(element) : false;
        });
    }

    function isFilesSurfaceVisible() {
        const element = document.getElementById('filesContainer')
            || document.getElementById('workspaceSectionFiles');
        return element ? isVisible(element) : false;
    }

    /** Register or replace a command in the single source-of-truth registry. */
    function registerCommand(command) {
        if (!command?.id || typeof command.run !== 'function') {
            throw new TypeError('Shortcut commands require a stable id and run function.');
        }
        commandRegistry.set(command.id, Object.freeze({
            group: 'general',
            keywords: [],
            palette: true,
            help: false,
            ...command,
        }));
        return () => commandRegistry.delete(command.id);
    }

    function isCommandAvailable(command) {
        if (typeof command.available !== 'function') return true;
        try {
            return command.available() !== false;
        } catch (error) {
            console.warn(`[shortcuts] Availability check failed for ${command.id}`, error);
            return false;
        }
    }

    function commandLabel(command) {
        return t(command.labelKey, command.labelFallback || command.id);
    }

    function commandDescription(command) {
        if (!command.descriptionKey && !command.descriptionFallback) return '';
        return t(command.descriptionKey, command.descriptionFallback || '');
    }

    function groupLabel(group) {
        const groups = {
            general: ['command_palette_group_general', 'General'],
            chat: ['command_palette_group_chat', 'Chat'],
            navigation: ['command_palette_group_navigation', 'Navigation'],
            workspace: ['command_palette_group_workspace', 'Workspace'],
        };
        const [key, fallback] = groups[group] || groups.general;
        return t(key, fallback);
    }

    function formatBindingParts(binding) {
        if (!binding) return [];
        if (binding.displayKey) return [t(binding.displayKey, binding.displayFallback || '')];

        let key = binding.label || binding.key || binding.code || '';
        const namedKeys = {
            escape: t('keyboard_shortcut_escape', 'Esc'),
            enter: t('keyboard_shortcut_enter', 'Enter'),
            arrowleft: '←',
            arrowright: '→',
            arrowup: '↑',
            arrowdown: '↓',
        };
        key = namedKeys[String(key).toLowerCase()] || String(key).replace(/^Key|^Digit/, '').toUpperCase();

        const parts = [];
        if (binding.primary) {
            parts.push(shortcutPlatform.usesMeta ? '⌘' : t('keyboard_shortcut_ctrl', 'Ctrl'));
        }
        if (binding.shift) {
            parts.push(shortcutPlatform.usesMeta ? '⇧' : t('keyboard_shortcut_shift', 'Shift'));
        }
        if (binding.alt) {
            parts.push(shortcutPlatform.usesMeta ? '⌥' : t('keyboard_shortcut_alt', 'Alt'));
        }
        parts.push(key);
        return parts;
    }

    function formatBinding(binding) {
        const parts = formatBindingParts(binding);
        return shortcutPlatform.usesMeta ? parts.join('') : parts.join(' + ');
    }

    function bindingToAria(binding) {
        if (!binding || binding.ariaOnly === false) return '';
        const parts = [];
        if (binding.primary) parts.push(shortcutPlatform.usesMeta ? 'Meta' : 'Control');
        if (binding.shift) parts.push('Shift');
        if (binding.alt) parts.push('Alt');
        const key = binding.key || String(binding.code || '').replace(/^Key|^Digit/, '');
        if (key) parts.push(key === ',' ? 'Comma' : key);
        return parts.join('+');
    }

    /**
     * Read trusted SVG markup from Omlorix's shared icon registry. Commands
     * registered by extensions can provide an icon name, but never raw markup.
     */
    function getPaletteIconMarkup(iconName) {
        if (typeof Icons === 'undefined' || !iconName) return '';
        return typeof Icons[iconName] === 'string' ? Icons[iconName] : '';
    }

    /** Choose a calm, recognizable leading glyph for every palette result. */
    function resultIconName(result) {
        if (result.type === 'chat') return 'chatFilesChooseChats';
        if (result.command?.icon) return result.command.icon;

        const commandId = String(result.command?.id || '');
        const workspaceIcon = commandId.startsWith('workspace.')
            ? {
                notifications: 'info',
                connections: 'connections',
                files: 'chatFiles',
                skills: 'skills_management',
                agents: 'assistant',
                todo: 'todo',
                notes: 'notes_management',
                memories: 'memory_management',
                prompts: 'textLines',
                bookmarks: 'bookmark',
            }[commandId.split('.')[1]]
            : '';
        if (workspaceIcon) return workspaceIcon;

        return {
            chat: 'chatFilesChooseChats',
            navigation: 'grid',
            workspace: 'workspace',
            general: 'settings',
        }[result.command?.group] || 'lightning';
    }

    /** Run a command after closing the palette and report async failures. */
    function runCommand(command, source = 'palette') {
        if (!command || !isCommandAvailable(command)) return false;
        // Palette actions move focus into their destination. Avoid restoring
        // the old launcher after the exit animation and stealing focus back.
        if (source === 'palette') closePalette({ restoreFocus: false });
        try {
            const result = command.run({ source });
            Promise.resolve(result).catch((error) => {
                console.error(`[shortcuts] Command failed: ${command.id}`, error);
            });
        } catch (error) {
            console.error(`[shortcuts] Command failed: ${command.id}`, error);
            return false;
        }
        return true;
    }

    function createPalette() {
        if (state.palette) return;

        const overlay = document.createElement('div');
        overlay.className = 'shortcut-palette-overlay shared-modal-overlay';
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        overlay.innerHTML = `
            <section class="shortcut-palette shared-modal shared-modal--fixed" role="dialog" aria-modal="true" aria-labelledby="shortcutPaletteTitle" tabindex="-1">
                <header class="shortcut-palette-header shared-modal-header shared-modal-header--search">
                    <h2 class="shortcut-palette-title shared-modal-title" id="shortcutPaletteTitle"></h2>
                    <span class="shortcut-palette-search-icon" aria-hidden="true"></span>
                    <input class="shortcut-palette-input" type="search" autocomplete="off" spellcheck="false"
                        role="combobox" aria-autocomplete="list" aria-expanded="true"
                        aria-controls="shortcutPaletteResults" aria-activedescendant="">
                    <div class="shortcut-palette-search-meta">
                        <span class="shortcut-palette-count" aria-hidden="true"></span>
                        <button type="button" class="shortcut-palette-close shared-modal-close" aria-label=""></button>
                    </div>
                </header>
                <div class="shortcut-palette-results shared-modal-body" id="shortcutPaletteResults" role="listbox"></div>
                <p class="shortcut-palette-status" role="status" aria-live="polite"></p>
                <footer class="shortcut-palette-footer shared-modal-footer">
                    <span class="shortcut-palette-footer-hint">
                        <span class="shortcut-palette-footer-keys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd></span>
                        <span data-shortcut-hint="navigate"></span>
                    </span>
                    <span class="shortcut-palette-footer-hint">
                        <kbd aria-hidden="true">↵</kbd>
                        <span data-shortcut-hint="open"></span>
                    </span>
                    <span class="shortcut-palette-footer-spacer"></span>
                    <span class="shortcut-palette-footer-hint">
                        <kbd aria-hidden="true">esc</kbd>
                        <span data-shortcut-hint="close"></span>
                    </span>
                </footer>
            </section>`;
        document.body.appendChild(overlay);

        state.palette = overlay;
        state.input = overlay.querySelector('.shortcut-palette-input');
        state.resultsHost = overlay.querySelector('.shortcut-palette-results');
        state.status = overlay.querySelector('.shortcut-palette-status');
        state.resultCount = overlay.querySelector('.shortcut-palette-count');

        // Icons are drawn from the shared registry so the palette stays in sync
        // with the rest of Omlorix instead of maintaining its own SVG copies.
        const searchIcon = overlay.querySelector('.shortcut-palette-search-icon');
        searchIcon.innerHTML = getPaletteIconMarkup('magnifyingGlass');

        overlay.querySelector('.shortcut-palette-close').addEventListener('click', () => closePalette());
        overlay.addEventListener('pointerdown', (event) => {
            if (event.target === overlay) closePalette();
        });
        overlay.addEventListener('click', (event) => {
            const resultButton = event.target.closest('[data-shortcut-result-index]');
            if (!resultButton) return;
            activateResult(Number(resultButton.dataset.shortcutResultIndex));
        });
        overlay.addEventListener('keydown', handlePaletteKeydown);
        state.input.addEventListener('input', () => refreshPaletteResults({ searchChats: true }));
        updatePaletteTranslations();

        if (typeof root.registerEscapeHandler === 'function') {
            root.registerEscapeHandler({
                id: 'shortcut-command-palette',
                priority: 250,
                isActive: () => state.paletteOpen,
                close: () => closePalette(),
            });
        }
    }

    function updatePaletteTranslations() {
        if (!state.palette) return;
        state.palette.querySelector('#shortcutPaletteTitle').textContent = t(
            'command_palette_title',
            'Commands and chat search'
        );
        const close = state.palette.querySelector('.shortcut-palette-close');
        close.setAttribute('aria-label', t('command_palette_close_aria', 'Close command palette'));
        close.innerHTML = getPaletteIconMarkup('close') || '<span aria-hidden="true">&times;</span>';
        state.input.placeholder = t('command_palette_placeholder', 'Search commands and chats…');
        state.input.setAttribute('aria-label', t('command_palette_placeholder', 'Search commands and chats…'));
        state.palette.querySelector('[data-shortcut-hint="navigate"]').textContent = t(
            'command_palette_hint_navigate',
            'navigate'
        );
        state.palette.querySelector('[data-shortcut-hint="open"]').textContent = t(
            'command_palette_hint_open',
            'open'
        );
        state.palette.querySelector('[data-shortcut-hint="close"]').textContent = t(
            'command_palette_hint_close',
            'close'
        );
        state.palette.querySelector('.shortcut-palette-footer').setAttribute(
            'aria-label',
            t('command_palette_hint', 'Use the arrow keys to move, Enter to open, and Escape to close.')
        );
    }

    function openPalette(options = {}) {
        createPalette();
        if (state.paletteOpen) {
            state.input.focus();
            state.input.select();
            return true;
        }

        // Reopening during the short exit animation should preserve the element
        // that originally launched the palette, not replace it with its input.
        const wasClosing = state.paletteClosing;
        state.paletteCloseSequence += 1;
        root.clearTimeout(state.paletteCloseTimer);
        state.paletteCloseTimer = 0;
        state.paletteClosing = false;
        state.palette.classList.remove('is-closing');
        if (!wasClosing) {
            state.returnFocus = document.activeElement instanceof HTMLElement
                ? document.activeElement
                : null;
            state.bodyHadModalOpen = document.body.classList.contains('modal-open');
        }
        state.paletteOpen = true;
        state.palette.hidden = false;
        state.palette.inert = false;
        state.palette.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
        state.input.value = String(options.query || '');
        state.activeIndex = 0;
        updatePaletteTranslations();
        refreshPaletteResults({ searchChats: Boolean(state.input.value.trim()) });
        requestAnimationFrame(() => {
            state.input.focus();
            state.input.select();
        });
        return true;
    }

    function closePalette({ restoreFocus = true } = {}) {
        if (!state.paletteOpen || !state.palette) return false;
        state.paletteOpen = false;
        state.paletteClosing = true;
        state.palette.classList.add('is-closing');
        state.palette.inert = true;
        state.palette.setAttribute('aria-hidden', 'true');
        if (restoreFocus && state.returnFocus?.isConnected) {
            state.returnFocus.focus();
        }
        clearTimeout(state.chatSearchTimer);
        state.chatSearchController?.abort();
        state.chatSearchController = null;
        state.chatSearchSequence += 1;

        // Keep the DOM mounted until the card finishes fading and moving out.
        // A sequence token prevents a stale completion from hiding a palette
        // that was reopened before its previous exit animation completed.
        const closeSequence = ++state.paletteCloseSequence;
        let handleAnimationEnd = null;
        const finishClose = () => {
            if (!state.paletteClosing || closeSequence !== state.paletteCloseSequence) return;
            root.clearTimeout(state.paletteCloseTimer);
            state.paletteCloseTimer = 0;
            if (handleAnimationEnd) {
                card?.removeEventListener('animationend', handleAnimationEnd);
            }
            state.paletteClosing = false;
            state.palette.classList.remove('is-closing');
            state.palette.hidden = true;
            if (!state.bodyHadModalOpen) document.body.classList.remove('modal-open');
            state.bodyHadModalOpen = false;
            state.returnFocus = null;
        };
        const card = state.palette.querySelector('.shortcut-palette');
        handleAnimationEnd = (event) => {
            if (event.target !== card) return;
            card.removeEventListener('animationend', handleAnimationEnd);
            finishClose();
        };
        card?.addEventListener('animationend', handleAnimationEnd);
        state.paletteCloseTimer = root.setTimeout(finishClose, 240);
        return true;
    }

    function availablePaletteCommands(query) {
        const normalizedQuery = normalizeSearchText(query);
        return Array.from(commandRegistry.values())
            .filter((command) => command.palette !== false && isCommandAvailable(command))
            .map((command, registryIndex) => {
                const label = commandLabel(command);
                const description = commandDescription(command);
                const haystack = normalizeSearchText([
                    label,
                    description,
                    ...(command.keywords || []).map((keyword) => t(keyword, keyword)),
                ].join(' '));
                let score = registryIndex + 100;
                if (!normalizedQuery) score = registryIndex;
                else if (normalizeSearchText(label).startsWith(normalizedQuery)) score = 0;
                else if (haystack.includes(normalizedQuery)) score = 20 + haystack.indexOf(normalizedQuery);
                else score = Number.POSITIVE_INFINITY;
                return { type: 'command', command, label, description, score };
            })
            .filter((result) => Number.isFinite(result.score))
            .sort((left, right) => left.score - right.score)
            .slice(0, 30);
    }

    function refreshPaletteResults({ searchChats = false } = {}) {
        if (!state.paletteOpen) return;
        const query = state.input.value.trim();
        state.results = availablePaletteCommands(query);
        state.activeIndex = Math.min(state.activeIndex, Math.max(0, state.results.length - 1));
        renderPaletteResults();

        clearTimeout(state.chatSearchTimer);
        state.chatSearchController?.abort();
        state.chatSearchController = null;
        const sequence = ++state.chatSearchSequence;
        if (!searchChats || normalizeSearchText(query).length < 2) return;

        state.chatSearchTimer = root.setTimeout(() => searchChatsForPalette(query, sequence), 180);
    }

    async function searchChatsForPalette(query, sequence) {
        if (!state.paletteOpen || typeof root.authedFetch !== 'function') return;
        const controller = new AbortController();
        state.chatSearchController = controller;
        state.status.textContent = t('command_palette_loading_chats', 'Searching chats…');
        const params = new URLSearchParams({ query, offset: '0', limit: '6' });
        let renderedResults = false;
        try {
            const response = await root.authedFetch(`/api/v1/chats/search?${params.toString()}`, {
                method: 'GET',
                signal: controller.signal,
            });
            if (!response.ok) return;
            const payload = await response.json();
            if (!state.paletteOpen || sequence !== state.chatSearchSequence) return;
            const items = Array.isArray(payload) ? payload : (payload.items || []);
            const chatResults = items.map((chat) => ({
                type: 'chat',
                id: chat?.chat_id ?? chat?.id ?? chat?.chatId,
                label: chat?.title || chat?.chat_title || chat?.name
                    || t('chat_reference_untitled', 'Untitled chat'),
                description: chat?.snippet || '',
                chat,
            })).filter((result) => result.id);
            state.results = [...availablePaletteCommands(query), ...chatResults];
            state.activeIndex = Math.min(state.activeIndex, Math.max(0, state.results.length - 1));
            renderPaletteResults();
            renderedResults = true;
        } catch (error) {
            if (error?.name !== 'AbortError') {
                console.warn('[shortcuts] Chat search failed', error);
            }
        } finally {
            if (sequence === state.chatSearchSequence && state.status && !renderedResults) {
                state.status.textContent = '';
            }
        }
    }

    function renderPaletteResults() {
        if (!state.resultsHost) return;
        state.resultsHost.replaceChildren();
        if (!state.results.length) {
            const empty = document.createElement('p');
            empty.className = 'shortcut-palette-empty search-modal-empty';
            empty.textContent = t('command_palette_empty', 'No matching commands or chats.');
            state.resultsHost.appendChild(empty);
            state.input.setAttribute('aria-activedescendant', '');
            state.status.textContent = t('command_palette_empty', 'No matching commands or chats.');
            state.resultCount.textContent = '';
            state.resultCount.classList.remove('visible');
            return;
        }

        const fragment = document.createDocumentFragment();
        let previousGroup = '';
        state.results.forEach((result, index) => {
            const group = result.type === 'chat' ? 'chats' : result.command.group;
            if (group !== previousGroup) {
                const heading = document.createElement('div');
                heading.className = 'shortcut-palette-group';
                heading.setAttribute('role', 'presentation');
                heading.textContent = result.type === 'chat'
                    ? t('command_palette_group_chats', 'Chats')
                    : groupLabel(group);
                fragment.appendChild(heading);
                previousGroup = group;
            }

            const button = document.createElement('button');
            button.type = 'button';
            button.tabIndex = -1;
            button.id = `shortcutPaletteResult${index}`;
            button.className = 'shortcut-palette-result';
            button.dataset.shortcutResultIndex = String(index);
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', index === state.activeIndex ? 'true' : 'false');

            const icon = document.createElement('span');
            icon.className = 'shortcut-palette-result-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.innerHTML = getPaletteIconMarkup(resultIconName(result));
            button.appendChild(icon);

            const copy = document.createElement('span');
            copy.className = 'shortcut-palette-result-copy';
            const label = document.createElement('span');
            label.className = 'shortcut-palette-result-label';
            label.textContent = result.label;
            copy.appendChild(label);
            if (result.description) {
                const description = document.createElement('span');
                description.className = 'shortcut-palette-result-description';
                description.textContent = result.description;
                copy.appendChild(description);
            }
            button.appendChild(copy);

            const binding = result.command?.binding;
            if (binding) {
                const shortcut = document.createElement('span');
                shortcut.className = 'shortcut-palette-result-shortcut';
                shortcut.setAttribute('aria-label', formatBinding(binding));
                formatBindingParts(binding).forEach((part) => {
                    const key = document.createElement('kbd');
                    key.setAttribute('aria-hidden', 'true');
                    key.textContent = part;
                    shortcut.appendChild(key);
                });
                button.appendChild(shortcut);
            } else if (result.type === 'chat') {
                const type = document.createElement('span');
                type.className = 'shortcut-palette-result-type';
                type.textContent = t('command_palette_chat_result', 'Chat');
                button.appendChild(type);
            }
            fragment.appendChild(button);
        });
        state.resultsHost.appendChild(fragment);
        updateActiveResult({ scroll: false });
        const countText = pluralTf(
            'command_palette_result_count',
            state.results.length,
            '{count} result',
            '{count} results',
        );
        state.status.textContent = countText;
        state.resultCount.textContent = countText;
        state.resultCount.classList.add('visible');
    }

    function updateActiveResult({ scroll = true } = {}) {
        const buttons = state.resultsHost?.querySelectorAll('[data-shortcut-result-index]') || [];
        buttons.forEach((button, index) => {
            button.setAttribute('aria-selected', index === state.activeIndex ? 'true' : 'false');
        });
        const active = buttons[state.activeIndex];
        state.input?.setAttribute('aria-activedescendant', active?.id || '');
        if (scroll) active?.scrollIntoView({ block: 'nearest' });
    }

    function activateResult(index) {
        const result = state.results[index];
        if (!result) return;
        if (result.type === 'command') {
            runCommand(result.command);
            return;
        }
        closePalette({ restoreFocus: false });
        if (typeof root.loadChatView === 'function') {
            root.loadChatView(result.id);
        } else {
            root.navigateTo?.(`/chat/${encodeURIComponent(result.id)}`);
        }
    }

    function handlePaletteKeydown(event) {
        if (!state.paletteOpen) return;
        // Handle Escape locally as well as through the optional global Escape
        // manager so embedded and reduced hosts can always dismiss the palette.
        if (event.key === 'Escape') {
            event.preventDefault();
            closePalette();
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!state.results.length) return;
            const delta = event.key === 'ArrowDown' ? 1 : -1;
            state.activeIndex = (state.activeIndex + delta + state.results.length) % state.results.length;
            updateActiveResult();
            return;
        }
        if (event.key === 'Home' || event.key === 'End') {
            event.preventDefault();
            if (!state.results.length) return;
            state.activeIndex = event.key === 'Home' ? 0 : state.results.length - 1;
            updateActiveResult();
            return;
        }
        if (event.key === 'Enter') {
            event.preventDefault();
            activateResult(state.activeIndex);
            return;
        }
        if (event.key === 'Tab') {
            const focusable = Array.from(state.palette.querySelectorAll('.shortcut-palette-close, .shortcut-palette-input'));
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
            }
        }
    }

    function openShortcutHelp() {
        if (typeof root.openUserSettings === 'function') {
            root.openUserSettings('help');
            return true;
        }
        return false;
    }

    function toggleUserSettings() {
        const view = document.getElementById('userSettingsView');
        if (view && isVisible(view)) {
            root.closeUserSettings?.();
            return true;
        }
        root.openUserSettings?.();
        return typeof root.openUserSettings === 'function';
    }

    function toggleModelDropdown() {
        const dropdown = document.getElementById('modelSelectDropdown');
        if (!dropdown) return false;
        if (typeof root.toggleModelSelect === 'function') root.toggleModelSelect();
        else if (dropdown.classList.contains('open')) root.closeModelSelect?.();
        else root.openModelSelect?.();
        requestAnimationFrame(() => {
            if (dropdown.classList.contains('open')) {
                document.getElementById('modelSelectSearch')?.focus();
            }
        });
        return true;
    }

    /**
     * Return the persisted chat currently rendered in the primary chat area.
     * Temporary and not-yet-created conversations intentionally have no id.
     */
    function getActiveChatId() {
        return String(document.getElementById('chatContainer')?.getAttribute('data-chat-id') || '').trim();
    }

    /**
     * Toggle the session-scoped temporary-chat choice through the existing
     * header control so its override state, button state, and notices stay in
     * sync. A saved chat cannot be converted into a temporary conversation.
     */
    function toggleTemporaryChatMode() {
        const button = document.getElementById('headerTempChatButton');
        if (!button || typeof root.setTemporaryChatMode !== 'function') return false;
        button.click();
        return true;
    }

    /** Apply one theme mode through the settings control that also persists it. */
    function applyThemeMode(mode) {
        const button = document.querySelector(`.theme-mode-input[data-theme-mode="${mode}"]`);
        if (button) {
            button.click();
            return true;
        }
        // Keep reduced or embedded hosts functional even if Settings markup is
        // unavailable. The common theme manager still persists local state.
        if (typeof root.setTheme === 'function') {
            root.setTheme(mode);
            return true;
        }
        return false;
    }

    function navigateToWorkspaceTab(tabId) {
        if (typeof root.showWorkspaceContainer !== 'function') return false;
        root.showWorkspaceContainer({ tab: tabId });
        return true;
    }

    function isWorkspaceTabAvailable(tabId) {
        if (typeof root.WorkspaceManager?.isTabAllowed === 'function'
            && root.WorkspaceManager.isTabAllowed(tabId) === false) return false;
        const tab = document.querySelector(`#mainHeaderWorkspace [data-workspace-tab="${tabId}"]`);
        return !tab || (!tab.hidden && tab.style.display !== 'none');
    }

    function registerBuiltInCommands() {
        registerCommand({
            id: 'palette.open',
            labelKey: 'command_palette_open',
            labelFallback: 'Open commands and chat search',
            descriptionKey: 'command_palette_open_desc',
            descriptionFallback: 'Find an app action or jump directly to a chat.',
            binding: { primary: true, key: 'k' },
            palette: false,
            help: true,
            helpGroup: 'global',
            allowPlainTextInput: true,
            run: () => openPalette(),
        });
        registerCommand({
            id: 'settings.toggle',
            labelKey: 'us_help_shortcuts_user_settings',
            labelFallback: 'Open user settings',
            descriptionKey: 'us_help_shortcuts_user_settings_desc',
            descriptionFallback: 'Open or close the settings view.',
            binding: { primary: true, shift: true, code: 'Comma', label: ',' },
            help: true,
            helpGroup: 'global',
            palette: false,
            run: toggleUserSettings,
        });

        const paletteCommands = [
            {
                id: 'chat.new', group: 'chat', labelKey: 'us_help_shortcuts_new_chat', labelFallback: 'Start a new chat',
                icon: 'plus',
                descriptionKey: 'us_help_shortcuts_new_chat_desc', descriptionFallback: 'Jump straight to creating a new chat.',
                run: () => root.showChatStartContainer?.(),
            },
            {
                id: 'chat.search', group: 'chat', labelKey: 'us_help_shortcuts_search', labelFallback: 'Search chats',
                icon: 'magnifyingGlass',
                descriptionKey: 'us_help_shortcuts_search_desc', descriptionFallback: 'Open the full chat search view.',
                run: () => root.showChatsSearchContainer?.(),
            },
            {
                id: 'chat.archived', group: 'chat', labelKey: 'archived_chats_title', labelFallback: 'Archived chats',
                icon: 'archive',
                run: () => document.getElementById('sidebarArchivedChats')?.click(),
            },
            {
                id: 'chat.model', group: 'chat', labelKey: 'us_help_shortcuts_model', labelFallback: 'Choose a model',
                icon: 'lightning',
                descriptionKey: 'us_help_shortcuts_model_desc', descriptionFallback: 'Open or close the model selection menu.',
                available: isChatSurfaceVisible, run: toggleModelDropdown,
            },
            {
                id: 'files.upload', group: 'chat', labelKey: 'us_help_shortcuts_upload', labelFallback: 'Upload files',
                icon: 'upload',
                descriptionKey: 'us_help_shortcuts_upload_desc', descriptionFallback: 'Open the file picker in chat or files view.',
                available: () => isChatSurfaceVisible() || isFilesSurfaceVisible(),
                run: () => {
                    const input = isChatSurfaceVisible()
                        ? document.getElementById('chatBoxFileInput')
                        : document.getElementById('fileInput');
                    input?.click();
                },
            },
            {
                id: 'chat.uploaded_files', group: 'chat', labelKey: 'us_help_shortcuts_choose_files', labelFallback: 'Choose uploaded files',
                icon: 'attachment_file',
                descriptionKey: 'us_help_shortcuts_choose_files_desc', descriptionFallback: 'Open uploaded files in the attachment menu.',
                available: () => isChatSurfaceVisible() && typeof root.ChatFilesMenu?.actions?.openUploadedFiles === 'function',
                run: () => root.ChatFilesMenu.actions.openUploadedFiles(),
            },
            {
                id: 'chat.dictation', group: 'chat', labelKey: 'us_help_shortcuts_dictation', labelFallback: 'Voice dictation',
                icon: 'microphone',
                descriptionKey: 'us_help_shortcuts_dictation_desc', descriptionFallback: 'Start or stop voice dictation.',
                available: () => isChatSurfaceVisible()
                    && isVisible(document.getElementById('chatBoxVoiceButton'))
                    && typeof root.handleDictationButtonClick === 'function',
                run: () => root.handleDictationButtonClick(),
            },
            {
                id: 'chat.stop', group: 'chat', labelKey: 'us_help_shortcuts_stop_generation', labelFallback: 'Stop current generation',
                icon: 'stop',
                descriptionKey: 'command_palette_stop_desc', descriptionFallback: 'Stop the response in the active send target.',
                available: () => typeof root.canCancelActiveGeneration === 'function'
                    && root.canCancelActiveGeneration({ scope: 'target' }),
                run: () => root.cancelActiveGeneration({ showVisualFeedback: true, scope: 'target' }),
            },
            {
                id: 'chat.temporary_toggle', group: 'chat',
                labelKey: 'command_palette_temporary_chat_toggle', labelFallback: 'Toggle temporary chat mode',
                icon: 'chatFilesChooseChats',
                descriptionKey: 'command_palette_temporary_chat_toggle_desc',
                descriptionFallback: 'Switch temporary mode for the next conversation on or off.',
                available: () => isChatSurfaceVisible()
                    && !getActiveChatId()
                    && root.chatSetup?.temporary_chat_allowed !== false
                    && isVisible(document.getElementById('headerTempChatButton'))
                    && typeof root.setTemporaryChatMode === 'function',
                run: toggleTemporaryChatMode,
            },
            {
                id: 'chat.share', group: 'chat', labelKey: 'chat_share_modal_title', labelFallback: 'Share chat',
                icon: 'share',
                descriptionKey: 'chat_share_modal_subtitle_default',
                descriptionFallback: 'Create a link to share this conversation.',
                available: () => Boolean(getActiveChatId())
                    && isVisible(document.getElementById('headerShareButton'))
                    && typeof root.ChatShareModal?.open === 'function',
                run: () => root.ChatShareModal.open(),
            },
            {
                id: 'chat.model_settings', group: 'chat',
                labelKey: 'header_open_model_settings', labelFallback: 'Open model settings',
                icon: 'settings',
                available: () => isChatSurfaceVisible()
                    && isVisible(document.getElementById('openModelSettingsButton'))
                    && typeof root.openModelSettingsSidebar === 'function',
                run: () => root.openModelSettingsSidebar(),
            },
            {
                id: 'navigation.files', group: 'navigation', labelKey: 'us_help_shortcuts_files', labelFallback: 'Navigate to Files',
                icon: 'chatFiles',
                descriptionKey: 'us_help_shortcuts_files_desc', descriptionFallback: 'Open the files view to manage your uploads.',
                run: () => root.showFilesContainer?.(),
            },
            {
                id: 'navigation.workspace', group: 'navigation', labelKey: 'us_help_shortcuts_workspace', labelFallback: 'Navigate to Workspace',
                icon: 'workspace',
                descriptionKey: 'us_help_shortcuts_workspace_desc', descriptionFallback: 'Open the workspace view for shared tools.',
                run: () => root.showWorkspaceContainer?.(),
            },
            {
                id: 'navigation.automations', group: 'navigation', labelKey: 'us_help_shortcuts_automations', labelFallback: 'Navigate to Automations',
                icon: 'automations_management',
                descriptionKey: 'us_help_shortcuts_automations_desc', descriptionFallback: 'Open the automations view.',
                available: () => root.enableAutomationsFeature === true,
                run: () => root.showAutomationsContainer?.(),
            },
            {
                id: 'navigation.projects', group: 'navigation', labelKey: 'us_help_shortcuts_projects', labelFallback: 'Navigate to Projects',
                icon: 'grid',
                descriptionKey: 'us_help_shortcuts_projects_desc', descriptionFallback: 'Open the projects view.',
                available: () => root.enableProjectsFeature === true,
                run: () => root.showProjectsContainer?.(),
            },
            {
                id: 'settings.open', group: 'general', labelKey: 'us_help_shortcuts_user_settings', labelFallback: 'Open user settings',
                icon: 'settings',
                descriptionKey: 'us_help_shortcuts_user_settings_desc', descriptionFallback: 'Open the settings view.',
                run: () => root.openUserSettings?.(),
            },
            {
                id: 'appearance.theme_system', group: 'general',
                labelKey: 'command_palette_theme_system', labelFallback: 'Theme: System',
                icon: 'sun',
                descriptionKey: 'us_appearance_theme_mode_desc', descriptionFallback: 'Select your preferred theme mode.',
                keywords: ['us_appearance_theme_title'],
                run: () => applyThemeMode('system'),
            },
            {
                id: 'appearance.theme_light', group: 'general',
                labelKey: 'command_palette_theme_light', labelFallback: 'Theme: Light',
                icon: 'sun',
                descriptionKey: 'us_appearance_theme_mode_desc', descriptionFallback: 'Select your preferred theme mode.',
                keywords: ['us_appearance_theme_title'],
                run: () => applyThemeMode('light'),
            },
            {
                id: 'appearance.theme_dark', group: 'general',
                labelKey: 'command_palette_theme_dark', labelFallback: 'Theme: Dark',
                icon: 'sun',
                descriptionKey: 'us_appearance_theme_mode_desc', descriptionFallback: 'Select your preferred theme mode.',
                keywords: ['us_appearance_theme_title'],
                run: () => applyThemeMode('dark'),
            },
            {
                id: 'account.logout', group: 'general', labelKey: 'us_btn_logout', labelFallback: 'Log out',
                icon: 'logout',
                available: () => typeof root.logout === 'function',
                run: () => root.logout(),
            },
        ];
        paletteCommands.forEach(registerCommand);

        const workspaceTabs = [
            'notifications', 'connections', 'files', 'skills', 'agents',
            'todo', 'notes', 'memories', 'prompts', 'bookmarks',
        ];
        workspaceTabs.forEach((tabId) => {
            const title = tabId.charAt(0).toUpperCase() + tabId.slice(1);
            registerCommand({
                id: `workspace.${tabId}`,
                group: 'workspace',
                labelKey: `workspace_tab_${tabId}`,
                labelFallback: title,
                descriptionKey: 'command_palette_workspace_tab_desc',
                descriptionFallback: 'Open this Workspace tab.',
                available: () => isWorkspaceTabAvailable(tabId),
                run: () => navigateToWorkspaceTab(tabId),
            });
        });
    }

    /** Build the help page from the same registry used by the keyboard router. */
    function renderShortcutHelp() {
        const host = document.getElementById('userSettingsShortcutsGrid');
        if (!host) return;
        host.replaceChildren();

        const helpItems = Array.from(commandRegistry.values())
            .filter((command) => command.help && command.binding && isCommandAvailable(command));
        const contextualItems = [
            {
                labelKey: 'us_help_shortcuts_send_message', labelFallback: 'Send message',
                descriptionKey: 'us_help_shortcuts_send_message_desc', descriptionFallback: 'Send the current draft when the composer uses Enter to send.',
                display: t('keyboard_shortcut_enter', 'Enter'), helpGroup: 'chat',
            },
            {
                labelKey: 'us_help_shortcuts_new_line', labelFallback: 'Insert a new line',
                descriptionKey: 'us_help_shortcuts_new_line_desc', descriptionFallback: 'Add a line break without sending the draft.',
                display: `${t('keyboard_shortcut_shift', 'Shift')} + ${t('keyboard_shortcut_enter', 'Enter')}`, helpGroup: 'chat',
            },
            {
                labelKey: 'us_help_shortcuts_send_alternate', labelFallback: 'Send with the primary modifier',
                descriptionKey: 'us_help_shortcuts_send_alternate_desc', descriptionFallback: 'Send when your preference uses Enter for new lines.',
                display: formatBinding({ primary: true, key: 'Enter' }), helpGroup: 'chat',
            },
            {
                labelKey: 'us_help_shortcuts_stop_generation', labelFallback: 'Stop current generation',
                descriptionKey: 'us_help_shortcuts_escape_stop_desc', descriptionFallback: 'Stop the active response when no menu or dialog needs to close first.',
                display: t('keyboard_shortcut_escape', 'Esc'), helpGroup: 'chat',
            },
            {
                labelKey: 'us_help_shortcuts_enter_twice_title', labelFallback: 'Stop after a send attempt',
                descriptionKey: 'us_help_shortcuts_stop_generation_desc', descriptionFallback: 'While a response is streaming and there is no queueable draft, press Enter twice quickly to stop it.',
                display: t('us_help_shortcuts_enter_twice', 'Enter, Enter'), helpGroup: 'chat',
            },
            {
                labelKey: 'us_help_shortcuts_dismiss_surface', labelFallback: 'Close the top surface',
                descriptionKey: 'us_help_shortcuts_dismiss_surface_desc', descriptionFallback: 'Close the active menu, dialog, or sheet before affecting the chat.',
                display: t('keyboard_shortcut_escape', 'Esc'), helpGroup: 'navigation',
            },
            {
                labelKey: 'us_help_shortcuts_move_results', labelFallback: 'Move through results',
                descriptionKey: 'us_help_shortcuts_move_results_desc', descriptionFallback: 'Move through command, chat-search, model, and tab results.',
                display: '↑ / ↓', helpGroup: 'navigation',
            },
            {
                labelKey: 'us_help_shortcuts_workspace_tabs', labelFallback: 'Move through Workspace tabs',
                descriptionKey: 'us_help_shortcuts_workspace_tabs_desc', descriptionFallback: 'Move focus between visible Workspace tabs; Home and End jump to the edges.',
                display: '← / → / ↑ / ↓', helpGroup: 'navigation',
            },
        ];

        const items = [
            ...helpItems.map((command) => ({
                labelKey: command.labelKey,
                labelFallback: command.labelFallback,
                descriptionKey: command.descriptionKey,
                descriptionFallback: command.descriptionFallback,
                display: formatBinding(command.binding),
                helpGroup: command.helpGroup || 'global',
            })),
            ...contextualItems,
        ];

        const groupOrder = ['global', 'chat', 'navigation'];
        const fragment = document.createDocumentFragment();
        groupOrder.forEach((groupId) => {
            const groupItems = items.filter((item) => item.helpGroup === groupId);
            if (!groupItems.length) return;
            const section = document.createElement('section');
            section.className = 'us-shortcuts-group';
            const heading = document.createElement('h3');
            heading.className = 'us-shortcuts-group-title';
            const headingKey = `us_help_shortcuts_group_${groupId}`;
            const headingFallback = { global: 'Everywhere', chat: 'In chat', navigation: 'Navigation and results' }[groupId];
            heading.textContent = t(headingKey, headingFallback);
            section.appendChild(heading);

            const list = document.createElement('ul');
            list.className = 'us-shortcuts-grid';
            list.setAttribute('role', 'list');
            groupItems.forEach((item) => {
                const card = document.createElement('li');
                card.className = 'us-shortcut-card';
                const copy = document.createElement('div');
                copy.className = 'us-shortcut-content';
                const title = document.createElement('h4');
                title.textContent = t(item.labelKey, item.labelFallback);
                const description = document.createElement('p');
                description.textContent = t(item.descriptionKey, item.descriptionFallback || '');
                copy.append(title, description);
                const badge = document.createElement('kbd');
                badge.className = 'us-shortcut-badge';
                badge.textContent = item.display;
                card.append(copy, badge);
                list.appendChild(card);
            });
            section.appendChild(list);
            fragment.appendChild(section);
        });
        host.appendChild(fragment);
    }

    function updateShortcutBadgesAndAria() {
        document.querySelectorAll('[data-shortcut-key]').forEach((element) => {
            const binding = {
                primary: element.dataset.shortcutPrimary !== 'false',
                key: element.dataset.shortcutKey,
                shift: String(element.dataset.shortcutModifiers || '').split(/\s+/).includes('shift'),
            };
            element.textContent = formatBinding(binding);
        });
        document.querySelectorAll('[data-shortcut-action]').forEach((element) => {
            const command = commandRegistry.get(element.dataset.shortcutAction);
            const ariaShortcut = bindingToAria(command?.binding);
            if (ariaShortcut) element.setAttribute('aria-keyshortcuts', ariaShortcut);
            else element.removeAttribute('aria-keyshortcuts');
        });
        renderShortcutHelp();
        updatePaletteTranslations();
    }

    function shouldAllowDirectCommand(event, command) {
        if (hasBlockingSurface()) return false;
        if (isProtectedEditorTarget(event.target)) return false;
        const editable = event.target?.closest?.('input, textarea, select');
        if (editable && command.allowPlainTextInput !== true) return false;
        return true;
    }

    function handleGlobalKeydown(event) {
        if (event.defaultPrevented || event.repeat || event.isComposing || event.key === 'Process') return;
        if (state.paletteOpen) {
            const paletteCommand = commandRegistry.get('palette.open');
            if (matchesBinding(event, paletteCommand?.binding, shortcutPlatform)) {
                event.preventDefault();
                event.stopPropagation();
                closePalette();
            }
            return;
        }

        // The normal index page registers Stop with the prioritized Escape
        // manager below. This branch is only a compatibility fallback for a
        // host that did not load that manager.
        if (event.key === 'Escape') {
            if (typeof root.registerEscapeHandler === 'function' || hasBlockingSurface()) return;
            if (typeof root.canCancelActiveGeneration === 'function'
                && root.canCancelActiveGeneration({ scope: 'target' })
                && typeof root.cancelActiveGeneration === 'function'
                && root.cancelActiveGeneration({ showVisualFeedback: true, scope: 'target' })) {
                event.preventDefault();
                event.stopPropagation();
            }
            return;
        }

        const directCommands = Array.from(commandRegistry.values()).filter((command) => command.binding);
        const command = directCommands.find((candidate) => (
            matchesBinding(event, candidate.binding, shortcutPlatform)
            && isCommandAvailable(candidate)
            && shouldAllowDirectCommand(event, candidate)
        ));
        if (!command) return;
        event.preventDefault();
        event.stopPropagation();
        runCommand(command, 'shortcut');
    }

    registerBuiltInCommands();
    if (typeof root.registerEscapeHandler === 'function') {
        root.registerEscapeHandler({
            id: 'active-generation-stop',
            priority: -100,
            isActive: () => (
                !state.paletteOpen
                && !hasBlockingSurface()
                && typeof root.canCancelActiveGeneration === 'function'
                && root.canCancelActiveGeneration({ scope: 'target' })
            ),
            close: () => root.cancelActiveGeneration?.({ showVisualFeedback: true, scope: 'target' }),
        });
    }
    document.addEventListener('keydown', handleGlobalKeydown);

    const initialize = () => {
        createPalette();
        updateShortcutBadgesAndAria();
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    } else {
        initialize();
    }
    document.addEventListener('i18n:updated', updateShortcutBadgesAndAria);
    document.addEventListener('chatSetupReady', updateShortcutBadgesAndAria);

    root.ChatShortcutManager = Object.freeze({
        register: registerCommand,
        getCommands: () => Array.from(commandRegistry.values()),
        openPalette,
        closePalette,
        openHelp: openShortcutHelp,
        formatBinding,
        refresh: updateShortcutBadgesAndAria,
        isPaletteOpen: () => state.paletteOpen,
    });
})(typeof window !== 'undefined' ? window : globalThis);
