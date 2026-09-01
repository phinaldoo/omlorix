


(() => {
    const $ = (id) => document.getElementById(id);
    const [
        input,
        clearButton,
        resultsHostSearch,
        resultsHost,
        loadingState,
        emptyStateNoResults,
        emptyStateNoChats,
        createBtn
    ] = [
        'chatsSearchInput',
        'chatsSearchClear',
        'chatsSearchResultsSearch',
        'chatsSearchResults',
        'chatsSearchLoading',
        'chatsSearchEmptyNoResults',
        'chatsSearchEmptyNoChats',
        'chatsSearchCreateBtn'
    ].map($);


    let debounceTimer = null;
    let activeController = null;

    // State management
    const searchState = {
        query: '',
        offset: 0,
        limit: 20,
        hasMore: true,
        loading: false
    };

    const defaultState = {
        offset: 0,
        limit: 20,
        hasMore: true,
        loading: false,
        grouped: false
    };

    const show = (el, display = 'flex') => el && (el.style.display = display);
    const hide = (el) => el && (el.style.display = 'none');
    const currentQuery = () => (input?.value || '').trim();

    // Sentinel for infinite scroll
    let searchObserver = null;
    let defaultObserver = null;

    function chatsSearchT(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    const chatTitleUtils = window.ChatTitleUtils || {};

    function chatsSearchTf(key, fallback, vars = {}) {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(chatsSearchT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function createSentinel() {
        const el = document.createElement('div');
        el.className = 'chats-search-sentinel';
        el.style.height = '10px';
        el.style.width = '100%';
        el.style.opacity = '0';
        return el;
    }

    function setupSearchObserver() {
        if (searchObserver) searchObserver.disconnect();

        searchObserver = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && searchState.hasMore && !searchState.loading) {
                performSearch(searchState.query, true);
            }
        }, { root: resultsHostSearch.parentElement, threshold: 0.1 });
    }

    function setupDefaultObserver() {
        if (defaultObserver) defaultObserver.disconnect();

        defaultObserver = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && defaultState.hasMore && !defaultState.loading) {
                loadMoreDefaultChats();
            }
        }, { root: resultsHost.parentElement, threshold: 0.1 });
    }

    function updateClearButtonVisibility(value = input?.value || '') {
        if (!clearButton) {
            return;
        }
        const shouldShow = value.trim().length > 0;
        clearButton.style.display = shouldShow ? 'flex' : 'none';
        clearButton.toggleAttribute('hidden', !shouldShow);
    }


    function parseLastUpdatedDate(isoString) {
        if (!isoString) {
            return null;
        }
        const normalized = String(isoString).trim();
        if (!normalized) {
            return null;
        }
        const normalizedWithZone = normalized.endsWith('Z') ? normalized : `${normalized}Z`;
        const date = new Date(normalizedWithZone);
        return Number.isNaN(date.getTime()) ? null : date;
    }


    function formatChatMeta(lastUpdatedAt) {
        const date = parseLastUpdatedDate(lastUpdatedAt);
        if (!date) {
            return '';
        }

        const diffMs = Date.now() - date.getTime();
        const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;

        if (diffMs > sevenDaysMs) {
            const day = new Intl.DateTimeFormat(undefined, { day: 'numeric' }).format(date);
            const month = new Intl.DateTimeFormat(undefined, { month: 'long' }).format(date);
            return `${day}. ${month}`;
        }

        if (typeof window.formatRelativeTime === 'function') {
            return window.formatRelativeTime(lastUpdatedAt);
        }

        return date.toLocaleString();
    }


    function escapeHtml(text) {
        if (text == null) {
            return '';
        }
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function highlightSnippet(text, query) {
        if (!text) {
            return '';
        }
        if (!query) {
            return escapeHtml(text);
        }
        const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        if (!escapedQuery) {
            return escapeHtml(text);
        }
        const regex = new RegExp(escapedQuery, 'ig');
        let lastIndex = 0;
        let result = '';
        let match;
        while ((match = regex.exec(text)) !== null) {
            const start = match.index;
            const end = start + match[0].length;
            if (end === start) {
                break;
            }
            if (lastIndex < start) {
                result += escapeHtml(text.slice(lastIndex, start));
            }
            result += '<strong class="chats-search-result-highlight">'
                + escapeHtml(text.slice(start, end))
                + '</strong>';
            lastIndex = end;
        }
        if (lastIndex < text.length) {
            result += escapeHtml(text.slice(lastIndex));
        }
        return result || escapeHtml(text);
    }


    function clearSearch() {
        if (!input) {
            return;
        }
        input.value = '';
        updateClearButtonVisibility('');
        showDefaultChats();
        requestAnimationFrame(() => input.focus());
    }

    function isPinnedChat(chat) {
        return chat?.pinned_position !== null && chat?.pinned_position !== undefined;
    }

    function pinnedPosition(chat) {
        const position = Number(chat?.pinned_position);
        return Number.isFinite(position) ? position : Number.MAX_SAFE_INTEGER;
    }

    function getSectionLabel(section) {
        if (section === 'pinned') {
            return typeof window.getTranslation === 'function'
                ? window.getTranslation('sidebar_section_pinned', 'Pinned')
                : 'Pinned';
        }
        return typeof window.getTranslation === 'function'
            ? window.getTranslation('chats_search_section_unpinned', 'Unpinned')
            : 'Unpinned';
    }

    function createDefaultSection(section) {
        const sectionEl = document.createElement('section');
        sectionEl.className = 'chats-search-section';
        sectionEl.dataset.chatsSearchSection = section;

        const title = document.createElement('p');
        title.className = 'chats-search-section-title';
        title.dataset.section = section;
        title.textContent = getSectionLabel(section);
        sectionEl.appendChild(title);

        const list = document.createElement('div');
        list.className = 'chats-search-section-list';
        list.dataset.sectionList = section;
        sectionEl.appendChild(list);

        return { sectionEl, list };
    }

    function updateDefaultSectionTitles() {
        resultsHost?.querySelectorAll?.('.chats-search-section-title[data-section]')?.forEach((title) => {
            title.textContent = getSectionLabel(title.dataset.section);
        });
    }

    function ensureDefaultSections(showUnpinned = true) {
        if (!resultsHost) {
            return {};
        }

        let pinnedSection = resultsHost.querySelector('[data-chats-search-section="pinned"]');
        let unpinnedSection = resultsHost.querySelector('[data-chats-search-section="unpinned"]');

        if (!pinnedSection) {
            const created = createDefaultSection('pinned');
            pinnedSection = created.sectionEl;
            resultsHost.insertBefore(pinnedSection, resultsHost.firstChild);
        }

        if (showUnpinned && !unpinnedSection) {
            const created = createDefaultSection('unpinned');
            unpinnedSection = created.sectionEl;
            resultsHost.appendChild(unpinnedSection);
        }

        if (unpinnedSection) {
            unpinnedSection.style.display = showUnpinned ? '' : 'none';
        }

        return {
            pinnedList: pinnedSection.querySelector('[data-section-list="pinned"]'),
            unpinnedList: unpinnedSection?.querySelector('[data-section-list="unpinned"]') || null
        };
    }

    function renderChats(items, host, query = '', showSnippet = true, append = false, includeSentinel = true) {
        if (!host) {
            return;
        }
        // If not appending, clear host
        if (!append) {
            host.innerHTML = '';
        } else {
            // Remove sentinel if exists before appending
            const sentinel = host.querySelector('.chats-search-sentinel');
            if (sentinel) sentinel.remove();
        }

        const list = Array.isArray(items) ? items : [];
        if (!list.length && !append) {
            return;
        }

        const fragment = document.createDocumentFragment();
        const allowDelete = (() => {
            try {
                const stored = window.localStorage?.getItem('allowChatDeletion');
                if (stored == null) {
                    return true;
                }
                const normalized = String(stored).trim().toLowerCase();
                if (['false', '0', 'no', 'off'].includes(normalized)) {
                    return false;
                }
                if (['true', '1', 'yes', 'on'].includes(normalized)) {
                    return true;
                }
                return Boolean(normalized);
            } catch (err) {
                console.warn('Unable to access localStorage for allowChatDeletion', err);
                return true;
            }
        })();

        list.forEach((chat) => {
            const chatId = chat?.chat_id ?? chat?.id ?? chat?.chatId;
            if (!chatId) {
                return;
            }
            // Check for duplicates if appending
            if (append && host.querySelector(`[data-chat-id="${chatId}"]`)) {
                return;
            }

            const card = document.createElement('div');
            card.className = 'chats-search-result can-show-actions';
            card.title = chatsSearchT('chats_search_result_hint', 'Select to open chat - Hover for actions');
            card.dataset.chatId = chatId;

            const topRow = document.createElement('div');
            topRow.className = 'chats-search-result-top';

            // Keep the primary navigation action separate from edit/delete so
            // the card never contains nested interactive controls.
            const contentContainer = document.createElement('button');
            contentContainer.type = 'button';
            contentContainer.className = 'chats-search-result-content chats-search-result-open';

            const title = document.createElement('span');
            title.className = 'chats-search-result-title chat-title-with-badge';
            const fallbackTitle = chatsSearchT('chat_reference_untitled', 'Untitled chat');
            const chatTitle = chatTitleUtils.getChatDisplayTitle?.(chat, fallbackTitle) || fallbackTitle;
            const highlightedTitle = highlightSnippet(chatTitle, query);
            title.innerHTML = chatTitleUtils.buildChatTitleMarkup?.(chat, highlightedTitle, { fallbackTitle }) || highlightedTitle;
            contentContainer.appendChild(title);

            const snippetText = chat.snippet || '';
            if (showSnippet && snippetText) {
                const snippet = document.createElement('span');
                snippet.className = 'chats-search-result-snippet';
                snippet.innerHTML = highlightSnippet(snippetText, query);
                contentContainer.appendChild(snippet);
            }

            topRow.appendChild(contentContainer);

            const aside = document.createElement('div');

            const metaText = chat.last_updated_at ? formatChatMeta(chat.last_updated_at) : '';
            if (metaText) {
                const meta = document.createElement('p');
                meta.className = 'chats-search-result-meta';
                meta.textContent = metaText;
                aside.appendChild(meta);
            }

            const actions = document.createElement('div');
            actions.className = 'chats-search-result-actions';

            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'chats-search-result-action chats-search-result-action-edit';
            editBtn.setAttribute('aria-label', chatsSearchT('chats_search_edit_chat', 'Edit chat'));
            editBtn.title = chatsSearchT('chats_search_edit_chat', 'Edit chat');
            editBtn.innerHTML = (typeof Icons !== 'undefined' && Icons?.edit) ? Icons.edit : chatsSearchT('projects_action_edit', 'Edit');
            editBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                const handler = window.showChatEditContainer;
                if (typeof handler === 'function') {
                    handler({ ...chat, id: chatId, chat_id: chatId, chatId });
                }
            });
            actions.appendChild(editBtn);

            if (allowDelete) {
                const deleteBtn = document.createElement('button');
                deleteBtn.type = 'button';
                deleteBtn.className = 'chats-search-result-action chats-search-result-action-delete';
                deleteBtn.setAttribute('aria-label', chatsSearchT('chats_search_delete_chat', 'Delete chat'));
                deleteBtn.title = chatsSearchT('chats_search_delete_chat', 'Delete chat');
                deleteBtn.innerHTML = (typeof Icons !== 'undefined' && Icons?.trash) ? Icons.trash : chatsSearchT('projects_action_delete', 'Delete');
                deleteBtn.addEventListener('mouseenter', () => deleteBtn.classList.add('color-red'));
                deleteBtn.addEventListener('mouseleave', () => deleteBtn.classList.remove('color-red'));
                deleteBtn.addEventListener('focus', () => deleteBtn.classList.add('color-red'));
                deleteBtn.addEventListener('blur', () => deleteBtn.classList.remove('color-red'));
                card.addEventListener('mouseenter', () => deleteBtn.classList.add('color-red'));
                card.addEventListener('mouseleave', () => deleteBtn.classList.remove('color-red'));
                deleteBtn.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    const handler = window.showChatDeleteContainer;
                    if (typeof handler === 'function') {
                        handler({ ...chat, id: chatId, chat_id: chatId, chatId });
                    }
                });
                actions.appendChild(deleteBtn);
            }

            aside.appendChild(actions);
            topRow.appendChild(aside);

            card.appendChild(topRow);

            fragment.appendChild(card);
        });
        host.appendChild(fragment);

        // Add sentinel if we have more
        const relevantState = host === resultsHostSearch ? searchState : defaultState;
        const relevantObserver = host === resultsHostSearch ? searchObserver : defaultObserver;

        if (includeSentinel && relevantState.hasMore) {
            const sentinel = createSentinel();
            host.appendChild(sentinel);
            if (relevantObserver) relevantObserver.observe(sentinel);
        }
    }

    function renderDefaultChats(items, append = false) {
        if (!resultsHost) {
            return;
        }

        const list = Array.isArray(items) ? items : [];
        if (!defaultState.grouped) {
            renderChats(list, resultsHost, '', false, append);
            return;
        }

        if (!append) {
            resultsHost.innerHTML = '';
        }

        const pinnedChats = list
            .filter(isPinnedChat)
            .sort((a, b) => pinnedPosition(a) - pinnedPosition(b));
        const unpinnedChats = list.filter((chat) => !isPinnedChat(chat));
        const showUnpinned = unpinnedChats.length > 0 || defaultState.hasMore;
        const { pinnedList, unpinnedList } = ensureDefaultSections(showUnpinned);

        if (!append) {
            renderChats(pinnedChats, pinnedList, '', false, false, false);
        }
        if (showUnpinned) {
            renderChats(unpinnedChats, unpinnedList, '', false, append, true);
        }
    }


    const abortSearch = () => {
        if (activeController) {
            activeController.abort();
            activeController = null;
        }
    };

    const stopDebounce = () => {
        if (debounceTimer) {
            clearTimeout(debounceTimer);
            debounceTimer = null;
        }
    };

    const resetSearchUi = () => {
        hide(loadingState);
        hide(emptyStateNoResults);
        if (resultsHostSearch) {
            // Only clear if we are not in load more mode (handled in performSearch)
            if (!searchState.loading) {
                resultsHostSearch.innerHTML = '';
                hide(resultsHostSearch);
            }

        }
    };

    const showDefaultChats = () => {
        abortSearch();
        stopDebounce();
        resetSearchUi();

        // Show defaults if we have them or if we can load them
        // But initially we might rely on what's rendered.
        // We will make sure resultsHost is visible.

        const hasChats = Boolean(resultsHost?.querySelector('.chats-search-result'));
        if (resultsHost) {
            resultsHost.style.display = hasChats ? 'flex' : 'none';
            if (hasChats) {
                // Trigger observer if needed
                const sentinel = resultsHost.querySelector('.chats-search-sentinel');
                if (sentinel && defaultObserver) defaultObserver.observe(sentinel);
            }
        }

        if (resultsHostSearch) hide(resultsHostSearch);
        if (emptyStateNoChats) {
            emptyStateNoChats.style.display = hasChats ? 'none' : 'flex';
        }
    };



    async function performSearch(query, isLoadMore = false) {
        if (!isLoadMore) {
            const controller = new AbortController();
            activeController = controller;
            show(loadingState);
            searchState.offset = 0;
            searchState.hasMore = true;
            searchState.query = query;
            // Clear previous results immediately for new search
            if (resultsHostSearch) resultsHostSearch.innerHTML = '';
        } else {
            // For load more, don't show global loading state maybe?
            // Or small indicator?
        }

        searchState.loading = true;

        const params = new URLSearchParams({
            query,
            offset: searchState.offset,
            limit: searchState.limit
        });

        try {
            const res = await window.authedFetch(`/api/v1/chats/search?${params.toString()}`, {
                method: 'GET',
                signal: activeController?.signal,
            });

            if (!res.ok) {
                // only notify if not aborted
                if (!activeController?.signal?.aborted) {
                    notifyError(chatsSearchTf('chats_search_failed_status', 'Failed to search chats: {status}', { status: res.status }));
                }
                return;
            }

            const data = await res.json();

            // Check if this is still the current query
            if (currentQuery() !== query && !isLoadMore) {
                return;
            }

            const items = Array.isArray(data?.items) ? data.items : [];
            const hasMore = data?.has_more === true;

            searchState.hasMore = hasMore;
            searchState.offset += items.length;
            searchState.loading = false;
            if (!isLoadMore) hide(loadingState);

            if (!items.length && !isLoadMore) {
                // No results at all
                if (resultsHostSearch) {
                    resultsHostSearch.innerHTML = '';
                    hide(resultsHostSearch);
                }
                hide(resultsHost);
                hide(emptyStateNoChats);
                show(emptyStateNoResults);
                return;
            }

            hide(emptyStateNoResults);
            hide(emptyStateNoChats);

            renderChats(items, resultsHostSearch, query, true, isLoadMore);
            show(resultsHostSearch);
            hide(resultsHost);

        } catch (err) {
            if (err?.name === 'AbortError') {
                return;
            }
            console.error('Failed to search chats', err);
            if (currentQuery() === query && !isLoadMore) {
                hide(loadingState);
                if (resultsHostSearch) {
                    resultsHostSearch.innerHTML = '';
                    hide(resultsHostSearch);
                }
                show(emptyStateNoResults);
            }
            searchState.loading = false;
        } finally {
            if (!isLoadMore && activeController) {
                // activeController = null; // Don't null early if we want to support subsequent requests, but here it's fine
            }
            if (currentQuery() === query && !isLoadMore) {
                hide(loadingState);
            }
        }
    }

    // Load more default chats (paginated history)
    async function loadMoreDefaultChats() {
        if (defaultState.loading) return;
        defaultState.loading = true;

        const params = new URLSearchParams({
            offset: defaultState.offset,
            limit: defaultState.limit
        });

        try {
            const res = await window.authedFetch(`/api/v1/chats/paginated?${params.toString()}`, {
                method: 'GET'
            });

            if (res.ok) {
                const data = await res.json();
                // data = { items: [], has_more: ..., pinned: ... }
                // We only care about items here usually, as pinned are loaded initially? 
                // Actually this endpoint returns everything.
                // For default view, we append 'items' (unpinned).

                const items = data.items || [];
                defaultState.hasMore = data.has_more ?? false;
                defaultState.offset += items.length;

                renderDefaultChats(items, true);
            }
        } catch (err) {
            console.error('Failed to load more default chats', err);
        } finally {
            defaultState.loading = false;
        }
    }



    function scheduleSearch(value) {
        const trimmed = value.trim();
        updateClearButtonVisibility(value);

        if (debounceTimer) {
            clearTimeout(debounceTimer);
            debounceTimer = null;
        }

        if (!trimmed.length) {
            // Cancel search
            showDefaultChats();
            return;
        }

        if (activeController) {
            activeController.abort();
            activeController = null;
        }

        hide(resultsHost);
        hide(emptyStateNoResults);
        hide(emptyStateNoChats);

        // Only clear and show loading if it's a new query start (not continuing typing maybe?)
        // But for debouncing we usually clear to show we are searching
        if (resultsHostSearch) {
            // We can keep old results until new ones arrive or clear immediately
            resultsHostSearch.innerHTML = '';
            hide(resultsHostSearch);
        }
        show(loadingState);

        debounceTimer = setTimeout(() => {
            debounceTimer = null;
            performSearch(trimmed);
        }, 300);
    }




    function initChatSearchList(chats) {
        // Initial load of default chats (usually passed from sidebar)
        const initialChats = Array.isArray(chats) ? [...chats] : [];

        if (input) {
            input.value = '';
        }
        updateClearButtonVisibility('');

        // Reset default state
        const unpinnedCount = initialChats.filter(c => c.pinned_position === null || c.pinned_position === undefined).length;
        defaultState.offset = unpinnedCount;
        defaultState.limit = 20;
        defaultState.hasMore = unpinnedCount >= defaultState.limit; // Assume more only if we filled the page
        defaultState.loading = false;
        defaultState.grouped = initialChats.some(isPinnedChat);

        // Init observers
        if (!searchObserver && resultsHostSearch) setupSearchObserver();
        if (!defaultObserver && resultsHost) setupDefaultObserver();

        renderDefaultChats(initialChats, false);
        showDefaultChats();

        if (input) {
            requestAnimationFrame(() => input.focus());
        }
    }

    window.initChatSearchList = initChatSearchList;
    window.focusChatsSearchInput = () => {
        if (!input) {
            return;
        }
        requestAnimationFrame(() => {
            input.focus();
            input.select?.();
        });
    };

    const handleResultClick = (event) => {
        const openButton = event?.target?.closest?.('.chats-search-result-open');
        if (!openButton) {
            return;
        }
        const card = openButton.closest('.chats-search-result');
        const chatId = card?.dataset?.chatId;
        if (!chatId) {
            return;
        }
        if (typeof window.loadChatView === 'function') {
            window.loadChatView(chatId);
        } else {
            window.navigateTo?.(`/chat/${encodeURIComponent(chatId)}`);
        }
    };

    /** Move between primary chat-result actions while leaving edit/delete in the tab order. */
    const handleResultNavigation = (event) => {
        const current = event.target.closest?.('.chats-search-result-open');
        if (!current || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        const host = current.closest('.chats-search-results');
        const buttons = Array.from(host?.querySelectorAll('.chats-search-result-open') || []);
        if (!buttons.length) return;
        const currentIndex = Math.max(0, buttons.indexOf(current));
        let nextIndex = currentIndex;
        if (event.key === 'Home') nextIndex = 0;
        if (event.key === 'End') nextIndex = buttons.length - 1;
        if (event.key === 'ArrowDown') nextIndex = Math.min(buttons.length - 1, currentIndex + 1);
        if (event.key === 'ArrowUp') nextIndex = currentIndex === 0 ? -1 : currentIndex - 1;
        event.preventDefault();
        if (nextIndex === -1) input?.focus();
        else buttons[nextIndex]?.focus();
    };

    const focusFirstResult = (event) => {
        if (event.key !== 'ArrowDown') return;
        const activeHost = currentQuery() ? resultsHostSearch : resultsHost;
        const firstResult = activeHost?.querySelector('.chats-search-result-open');
        if (!firstResult) return;
        event.preventDefault();
        firstResult.focus();
    };

    createBtn?.addEventListener('click', () => showChatStartContainer?.());
    clearButton?.addEventListener('click', clearSearch);
    input?.addEventListener('input', (event) => scheduleSearch(event.target.value));
    input?.addEventListener('keydown', focusFirstResult);
    resultsHost?.addEventListener('click', handleResultClick);
    resultsHostSearch?.addEventListener('click', handleResultClick);
    resultsHost?.addEventListener('keydown', handleResultNavigation);
    resultsHostSearch?.addEventListener('keydown', handleResultNavigation);
    document.addEventListener('i18n:updated', updateDefaultSectionTitles);
})();
