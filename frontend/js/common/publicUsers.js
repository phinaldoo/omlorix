(function () {
    function uniqueUsers(users) {
        const byId = new Map();
        for (const user of users) {
            const id = String(user?.id || '').trim();
            if (id && !byId.has(id)) byId.set(id, user);
        }
        return [...byId.values()];
    }

    // Fetch one bounded page. Never drain the directory when opening a picker.
    async function fetchPage({ request = window.authedFetch, q = '', limit = 100, offset = 0, signal, errorMessage, resolveError } = {}) {
        const params = new URLSearchParams({
            limit: String(Math.min(100, Math.max(1, Math.floor(Number(limit) || 100)))),
            offset: String(Math.max(0, Math.floor(Number(offset) || 0))),
        });
        const query = String(q).trim().slice(0, 100);
        if (query) params.set('q', query);
        const response = await request(`/api/v1/users/public-users?${params}`, {
            method: 'GET', credentials: 'include', signal,
        });
        if (!response.ok) {
            const payload = resolveError ? await response.json().catch(() => ({})) : null;
            throw new Error(resolveError ? resolveError(payload, errorMessage) : errorMessage);
        }
        const payload = await response.json();
        const users = Array.isArray(payload) ? payload : [];
        return {
            users: uniqueUsers(users),
            nextOffset: Number(params.get('offset')) + users.length,
            hasMore: users.length > 0 && String(response.headers.get('X-Has-More') || '').toLowerCase() === 'true',
        };
    }

    function createPicker({ list, fetchPage: requestPage, render, onUsers, selectedUsers, loadingMessage, errorMessage }) {
        let query = null;
        let users = [];
        let nextOffset = 0;
        let hasMore = false;
        let loading = false;
        let error = null;
        let timer;
        let controller;
        let generation = 0;
        let disposed = false;

        function refresh() {
            if (disposed || !list.isConnected) return;
            const focusedId = list.contains(document.activeElement) ? document.activeElement?.dataset?.userId : null;
            render(users);
            list.setAttribute('aria-busy', String(loading));
            if (loading || error) {
                const status = document.createElement('div');
                status.className = 'cs-invite-state';
                status.setAttribute('role', error ? 'alert' : 'status');
                status.textContent = error || loadingMessage;
                if (!users.length) list.replaceChildren(status);
                else list.appendChild(status);
            }
            if (hasMore || error) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'om-button border';
                button.textContent = error
                    ? window.getTranslation('chat_load_retry', 'Retry')
                    : window.getTranslation('common_load_more', 'Load more');
                button.disabled = loading;
                button.addEventListener('click', () => void load(true));
                list.appendChild(button);
            }
            if (focusedId) {
                [...list.querySelectorAll('[data-user-id]')].find((item) => item.dataset.userId === focusedId)?.focus({ preventScroll: true });
            }
        }

        async function load(restoreFocus = false) {
            if (disposed || loading || !list.isConnected) return;
            const currentGeneration = generation;
            const previousCount = users.length;
            controller = new AbortController();
            loading = true;
            error = null;
            refresh();
            try {
                const page = await requestPage({ q: query, offset: nextOffset, signal: controller.signal });
                if (disposed || currentGeneration !== generation) return;
                users = uniqueUsers([...users, ...page.users]);
                nextOffset = page.nextOffset;
                hasMore = page.hasMore;
                // Retain selected chips across searches without caching every query.
                onUsers(uniqueUsers([...selectedUsers(), ...users]));
            } catch (failure) {
                if (disposed || currentGeneration !== generation || failure?.name === 'AbortError') return;
                error = failure?.message || errorMessage;
            } finally {
                if (!disposed && currentGeneration === generation) {
                    loading = false;
                    refresh();
                    if (restoreFocus && list.isConnected) {
                        const items = list.querySelectorAll('[data-user-id]');
                        (items[previousCount] || list.querySelector('button:last-child'))?.focus({ preventScroll: true });
                    }
                }
            }
        }

        function search(value = '', { immediate = false } = {}) {
            const nextQuery = String(value).trim().slice(0, 100);
            if (disposed || nextQuery === query) return;
            query = nextQuery;
            generation += 1;
            controller?.abort();
            clearTimeout(timer);
            users = [];
            nextOffset = 0;
            hasMore = false;
            loading = false;
            error = null;
            if (immediate) return load();
            timer = setTimeout(() => void load(), 250);
        }

        function dispose() {
            disposed = true;
            generation += 1;
            clearTimeout(timer);
            controller?.abort();
            list.removeAttribute('aria-busy');
        }

        return { search, refresh, dispose };
    }

    window.PublicUsers = { fetchPage, createPicker };
})();
